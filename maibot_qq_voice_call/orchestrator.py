"""Call lifecycle orchestration for the chained ASR -> LLM -> TTS path."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import ClientSession
from maibot_sdk import PluginContext

from .audio import AudioSegmenter
from .bridge import BridgeClient
from .chat import MaiBotPhoneChat
from .config import QQVoiceCallConfig
from .context import CallerContextResolver
from .models import CallerContext, CallUtterance, RuntimeStatus
from .providers import (
    DashScopeRealtimeASR,
    DashScopeRealtimeTTS,
    TTSPlaybackInterrupted,
)
from .text import WAIT_TOKEN, TranscriptGate, clean_tts_text

ReadyCallback = Callable[[bool], Awaitable[None]]


class CallOrchestrator:
    def __init__(
        self,
        ctx: PluginContext,
        config: QQVoiceCallConfig,
        logger,
    ) -> None:
        self.ctx = ctx
        self.config = config
        self.logger = logger
        self.status = RuntimeStatus()
        self.http_session: ClientSession | None = None
        self.bridge: BridgeClient | None = None
        self.chat = MaiBotPhoneChat(ctx, config.chat)
        self.context_resolver = CallerContextResolver(ctx, config.chat)
        self.tts: DashScopeRealtimeTTS | None = None
        self.streaming_asr: DashScopeRealtimeASR | None = None
        self.segmenter = AudioSegmenter(
            config.audio,
            self.status,
            logger,
            on_speech_started=self.stop_speaking,
        )
        self.transcript_gate = TranscriptGate(config.chat.pending_transcript_seconds)
        self.active_call = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.utterance_queue: asyncio.Queue[CallUtterance] = asyncio.Queue(maxsize=1)
        self.response_lock = asyncio.Lock()
        self.close_lock = asyncio.Lock()
        self.closed = False
        self.context_active_invite = ""
        self.last_greeted_invite = ""
        self.caller_context = CallerContext()

    async def startup(self) -> None:
        if self.config.asr.backend not in {"dashscope-realtime", "maibot"}:
            raise RuntimeError("asr.backend 只能是 dashscope-realtime 或 maibot")
        if self.config.tts.backend != "dashscope-realtime":
            raise RuntimeError("tts.backend 当前只支持 dashscope-realtime")

        self.http_session = ClientSession()
        self.bridge = BridgeClient(self.http_session, self.config.bridge)
        if not await self.bridge.health():
            raise RuntimeError("无法连接 QQ 通话桥，请检查 bridge.base_url 与鉴权 Token")

        self.tts = DashScopeRealtimeTTS(
            self.http_session,
            self.config.tts,
            self.config.audio,
            self.status,
            self.logger,
        )
        await self.tts.startup()

        if self.config.asr.backend == "dashscope-realtime":
            streaming_asr = DashScopeRealtimeASR(
                self.http_session,
                self.config.asr,
                self.config.audio,
                self.status,
                self.logger,
            )
            try:
                await streaming_asr.startup()
            except Exception as exc:
                self.logger.warning(
                    "Qwen 实时 ASR 预热失败，继续使用 MaiBot ASR 兜底: %s",
                    exc,
                )
                await streaming_asr.close()
            else:
                self.streaming_asr = streaming_asr
                self.segmenter.streaming_asr = streaming_asr
        self.status.ready = True

    async def close(self) -> None:
        async with self.close_lock:
            if self.closed:
                return
            self.closed = True
            self.stop_event.set()
            self.active_call.clear()
            await self.segmenter.close()
            if self.streaming_asr is not None:
                await self.streaming_asr.close()
            if self.tts is not None:
                await self.tts.close()
            if self.http_session is not None:
                await self.http_session.close()
            self.status.ready = False
            self.status.active = False

    async def run(self, ready_callback: ReadyCallback | None = None) -> None:
        tasks: list[asyncio.Task[None]] = []
        try:
            await self.startup()
            if ready_callback is not None:
                await ready_callback(True)
            tasks = [
                asyncio.create_task(self.monitor_calls(), name="qq-call-monitor"),
                asyncio.create_task(
                    self.segmenter.run(
                        self.active_call,
                        self.utterance_queue,
                        self.stop_event,
                    ),
                    name="qq-call-audio",
                ),
                asyncio.create_task(
                    self.process_utterances(),
                    name="qq-call-turns",
                ),
            ]
            await self.stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.close()
            if ready_callback is not None:
                with contextlib.suppress(Exception):
                    await ready_callback(False)

    async def transcribe(self, wav_bytes: bytes) -> str:
        result = await self.ctx.llm.transcribe_audio(audio=wav_bytes)
        if not isinstance(result, dict) or not result.get("success"):
            error = result.get("error") if isinstance(result, dict) else result
            raise RuntimeError(f"MaiBot ASR 调用失败: {error or 'unknown error'}")
        return str(result.get("text") or result.get("content") or "").strip()

    async def prepare_caller_context(
        self,
        invite_at: str,
        call: dict[str, Any],
    ) -> None:
        try:
            snapshot = await self.context_resolver.resolve(
                call,
                account_id=self.config.plugin.account_id,
                scope=self.config.plugin.scope,
            )
        except Exception as exc:
            self.logger.warning("读取 MaiBot 来电者上下文失败，使用 QQ 身份兜底: %s", exc)
            snapshot = CallerContext(
                uid=str(call.get("callerUid") or "").strip(),
                uin=str(call.get("callerUin") or "").strip(),
                name=str(call.get("callerName") or "").strip() or "用户",
                prompt_context=(
                    "【来电者上下文】只确认了本次 QQ 来电身份，MaiBot 记忆暂时不可用。"
                    "不要猜测没有提供的经历或关系。"
                ),
            )
        if self.status.invite_at != invite_at:
            return
        self.caller_context = snapshot
        self.context_active_invite = invite_at
        self.chat.reset(snapshot)
        self.status.caller_uid = snapshot.uid
        self.status.caller_uin = snapshot.uin
        self.status.caller_name = snapshot.name
        self.status.caller_person_id = snapshot.person_id
        self.status.caller_stream_id = snapshot.stream_id
        self.status.caller_context_ready = bool(snapshot.uin)
        self.logger.info(
            "来电者上下文已准备: name=%s uin=%s person=%s recent=%d",
            snapshot.name,
            snapshot.uin or "(unknown)",
            snapshot.person_id or "(unknown)",
            snapshot.recent_message_count,
        )

    async def speak(self, text: str) -> None:
        if self.tts is None:
            raise RuntimeError("TTS 客户端尚未初始化")
        spoken_text = clean_tts_text(
            text,
            max_chars=self.config.chat.max_reply_chars,
        )
        if not spoken_text:
            return
        try:
            first_audio_seconds = await self.tts.start(spoken_text)
        except TTSPlaybackInterrupted:
            self.logger.info("TTS 在首包前被来电者插话打断")
            return
        self.status.last_tts_seconds = round(first_audio_seconds, 3)
        self.logger.info("TTS 首个音频包耗时 %.3f 秒", first_audio_seconds)

    async def stop_speaking(self) -> bool:
        if self.tts is None:
            return False
        try:
            return await self.tts.stop()
        except Exception as exc:
            self.logger.debug("停止上一段 TTS 失败: %s", exc)
            return False

    async def _handle_transcript(self, transcript: str) -> None:
        self.status.last_transcript = transcript
        if self.config.plugin.log_transcripts:
            self.logger.info("用户说: %s", transcript)

        accepted = self.transcript_gate.process(transcript)
        self.status.pending_transcript = self.transcript_gate.pending_text
        if accepted is None:
            self.status.ignored_utterance_count += 1
            return
        self.status.utterance_count += 1
        self.status.last_transcript = accepted

        started_at = time.perf_counter()
        reply = await self.chat.ask(accepted)
        self.status.last_chat_seconds = round(time.perf_counter() - started_at, 3)
        if reply == WAIT_TOKEN:
            self.status.ignored_utterance_count += 1
            self.status.last_reply = ""
            return
        self.status.last_reply = reply
        if self.config.plugin.log_transcripts:
            self.logger.info("通话回复: %s", reply)
        if self.active_call.is_set():
            await self.speak(reply)

    async def process_utterances(self) -> None:
        while not self.stop_event.is_set():
            utterance = await self.utterance_queue.get()
            self.status.queue_size = self.utterance_queue.qsize()
            if not self.active_call.is_set():
                continue
            async with self.response_lock:
                self.status.busy = True
                try:
                    await self.stop_speaking()
                    transcript = ""
                    realtime_failed = False
                    if utterance.realtime_transcript is not None:
                        try:
                            transcript, asr_seconds = await asyncio.wait_for(
                                asyncio.shield(utterance.realtime_transcript),
                                timeout=self.config.asr.final_timeout_seconds,
                            )
                            self.status.last_asr_seconds = round(asr_seconds, 3)
                        except Exception as exc:
                            realtime_failed = True
                            utterance.realtime_transcript.add_done_callback(
                                AudioSegmenter._consume_transcript
                            )
                            self.logger.warning(
                                "实时 ASR 结果不可用，回退 MaiBot ASR: %s",
                                exc,
                            )
                    if utterance.realtime_transcript is None or realtime_failed:
                        started_at = time.perf_counter()
                        transcript = await self.transcribe(utterance.wav_bytes)
                        self.status.last_asr_seconds = round(
                            time.perf_counter() - started_at,
                            3,
                        )
                    if transcript:
                        await self._handle_transcript(transcript)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.status.last_error = str(exc)
                    self.logger.exception("处理通话语音失败")
                finally:
                    self.status.busy = False

    async def greet(self, invite_at: str) -> None:
        async with self.response_lock:
            if not self.active_call.is_set() or self.status.invite_at != invite_at:
                return
            try:
                await self.speak(self.config.chat.greeting)
            except Exception as exc:
                self.status.last_error = str(exc)
                self.logger.exception("播放通话问候语失败")

    def _reset_call_state(self) -> None:
        self.active_call.clear()
        self.status.active = False
        self.status.caller_context_ready = False
        self.context_active_invite = ""
        self.transcript_gate.clear()
        self.status.pending_transcript = ""

    async def monitor_calls(self) -> None:
        assert self.bridge is not None
        while not self.stop_event.is_set():
            try:
                call = await self.bridge.current_call()
                phase = str(call.get("phase") or "unknown")
                invite_at = str(call.get("inviteAt") or "")
                self.status.call_phase = phase
                self.status.invite_at = invite_at
                if phase == "connected" and invite_at:
                    if self.context_active_invite != invite_at:
                        await self.prepare_caller_context(invite_at, call)
                    self.active_call.set()
                    self.status.active = True
                    if self.last_greeted_invite != invite_at:
                        self.last_greeted_invite = invite_at
                        self.logger.info("QQ 语音通话已进入房间")
                        asyncio.create_task(self.greet(invite_at))
                elif phase in {"ringing", "accepting", "accepted"}:
                    self.active_call.clear()
                    self.status.active = False
                else:
                    self._reset_call_state()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.last_error = str(exc)
                self.logger.warning("查询或处理通话状态失败: %s", exc)
            await asyncio.sleep(self.config.bridge.poll_interval_seconds)

    async def manual_chat(self, text: str, *, speak: bool = False) -> dict[str, Any]:
        text = text.strip()
        if not text:
            return {"success": False, "error": "text 不能为空"}
        async with self.response_lock:
            started_at = time.perf_counter()
            reply = await self.chat.ask(text)
            self.status.last_chat_seconds = round(time.perf_counter() - started_at, 3)
            spoken = False
            if speak and reply != WAIT_TOKEN and self.active_call.is_set():
                await self.speak(reply)
                spoken = True
            return {
                "success": True,
                "reply": reply,
                "spoken": spoken,
            }
