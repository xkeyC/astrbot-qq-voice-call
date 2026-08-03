"""Stream Qwen3 cloned speech directly into the QQ microphone sink."""

from __future__ import annotations

import array
import asyncio
import base64
import contextlib
import json
import math
import os
import time
import uuid
from typing import Any

from aiohttp import ClientSession, ClientWSTimeout, WSMsgType

from ..config import AudioSection, TTSSection
from ..models import RuntimeStatus
from .asr_dashscope import _model_url


class TTSPlaybackInterrupted(Exception):
    """Raised when barge-in interrupts a TTS turn before its first audio packet."""


class DashScopeRealtimeTTS:
    def __init__(
        self,
        session: ClientSession,
        config: TTSSection,
        audio: AudioSection,
        status: RuntimeStatus,
        logger,
    ) -> None:
        self.session = session
        self.config = config
        self.audio = audio
        self.status = status
        self.logger = logger
        self.api_key = os.getenv(config.api_key_env, "").strip()
        self.voice = os.getenv(config.voice_id_env, "").strip() or config.voice_id.strip()
        if not self.api_key:
            raise RuntimeError(f"实时 TTS API Key 为空: {config.api_key_env}")
        if not self.voice:
            raise RuntimeError(
                f"实时 TTS 克隆音色为空；请设置 tts.voice_id 或 {config.voice_id_env}"
            )
        self.url = _model_url(config.websocket_base_url, config.model)
        self.state_lock = asyncio.Lock()
        self.connection_lock = asyncio.Lock()
        self.turn_lock = asyncio.Lock()
        self.playback_process: asyncio.subprocess.Process | None = None
        self.websocket: Any | None = None
        self.receiver_task: asyncio.Task[None] | None = None
        self.warm_task: asyncio.Task[None] | None = None
        self.ready_future: asyncio.Future[None] | None = None
        self.first_audio_future: asyncio.Future[float] | None = None
        self.response_active = False
        self.response_started_at = 0.0
        self.playback_until = 0.0
        self.closed = False
        self.generation = 0

    @staticmethod
    def event(event_type: str, **payload: object) -> dict[str, object]:
        return {
            "event_id": f"event_{uuid.uuid4().hex}",
            "type": event_type,
            **payload,
        }

    def gain_pcm16(
        self,
        audio: bytes,
        *,
        knee_dbfs: float = -6.0,
        ceiling_dbfs: float = -1.0,
    ) -> bytes:
        """Apply linear gain with a soft peak limiter."""

        if not audio or self.config.gain_db == 0:
            return audio
        gain = 10 ** (self.config.gain_db / 20)
        knee = 10 ** (knee_dbfs / 20)
        ceiling = 10 ** (ceiling_dbfs / 20)
        samples = array.array("h")
        samples.frombytes(audio)
        for index, sample in enumerate(samples):
            amplified = sample / 32768 * gain
            sign = -1 if amplified < 0 else 1
            magnitude = abs(amplified)
            if magnitude > knee:
                width = ceiling - knee
                magnitude = knee + width * math.tanh((magnitude - knee) / width)
            limited = sign * min(magnitude, ceiling)
            samples[index] = max(-32768, min(32767, round(limited * 32767)))
        return samples.tobytes()

    def _consume_task_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self.logger.error(
                "DashScope 实时 TTS 后台播放失败: %s",
                exception,
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    async def _spawn_playback_process(self) -> asyncio.subprocess.Process:
        process_env = os.environ.copy()
        if self.audio.pulse_server:
            process_env["PULSE_SERVER"] = self.audio.pulse_server
        return await asyncio.create_subprocess_exec(
            "pacat",
            "--playback",
            "--raw",
            "--format=s16le",
            f"--rate={self.config.sample_rate}",
            "--channels=1",
            f"--device={self.audio.playback_device}",
            "--client-name=maibot-qq-call-tts",
            f"--latency-msec={max(20, self.config.playback_latency_ms)}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=process_env,
        )

    @staticmethod
    async def _terminate_process(
        process: asyncio.subprocess.Process | None,
    ) -> None:
        if process is None:
            return
        if process.stdin is not None:
            with contextlib.suppress(BrokenPipeError):
                process.stdin.close()
        if process.returncode is not None:
            return
        process.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=1)
        if process.returncode is None:
            process.kill()
            await process.wait()

    async def _disconnect_locked(self, *, interrupted: bool = False) -> None:
        async with self.state_lock:
            self.generation += 1
            task = self.receiver_task
            process = self.playback_process
            websocket = self.websocket
            ready_future = self.ready_future
            first_audio_future = self.first_audio_future
            self.receiver_task = None
            self.playback_process = None
            self.websocket = None
            self.ready_future = None
            self.first_audio_future = None
            self.response_active = False
            self.response_started_at = 0.0
            self.playback_until = 0.0
            self.status.tts_connected = False
        if ready_future is not None and not ready_future.done():
            ready_future.cancel()
        if first_audio_future is not None and not first_audio_future.done():
            if interrupted:
                first_audio_future.set_exception(TTSPlaybackInterrupted())
            else:
                first_audio_future.cancel()
        if websocket is not None and not websocket.closed:
            with contextlib.suppress(Exception):
                await websocket.close()
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._terminate_process(process)

    async def _receive_events(
        self,
        websocket: Any,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        session_ready = False
        try:
            async for message in websocket:
                if message.type == WSMsgType.TEXT:
                    payload = json.loads(message.data)
                    event_type = str(payload.get("type") or "")
                    if event_type == "session.created":
                        await websocket.send_json(
                            self.event(
                                "session.update",
                                session={
                                    "voice": self.voice,
                                    "mode": "commit",
                                    "language_type": "Chinese",
                                    "response_format": "pcm",
                                    "sample_rate": self.config.sample_rate,
                                    "volume": 100,
                                    "speech_rate": self.config.speech_rate,
                                },
                            )
                        )
                    elif event_type == "session.updated":
                        session_ready = True
                        async with self.state_lock:
                            ready_future = self.ready_future
                            self.status.tts_connected = True
                        if ready_future is not None and not ready_future.done():
                            ready_future.set_result(None)
                    elif event_type == "response.audio.delta":
                        async with self.state_lock:
                            if generation != self.generation or not self.response_active:
                                continue
                            first_audio = self.first_audio_future
                            started_at = self.response_started_at
                        encoded_audio = str(payload.get("delta") or "")
                        audio = self.gain_pcm16(base64.b64decode(encoded_audio))
                        if process.stdin is None:
                            raise RuntimeError("QQ 麦克风播放管道不可用")
                        process.stdin.write(audio)
                        await process.stdin.drain()
                        now = time.monotonic()
                        async with self.state_lock:
                            self.playback_until = max(now, self.playback_until) + len(
                                audio
                            ) / (self.config.sample_rate * 2)
                        if first_audio is not None and not first_audio.done():
                            first_audio.set_result(time.perf_counter() - started_at)
                    elif event_type == "response.done":
                        async with self.state_lock:
                            first_audio = self.first_audio_future
                            self.response_active = False
                        if first_audio is not None and not first_audio.done():
                            first_audio.set_exception(
                                RuntimeError("DashScope 实时 TTS 未返回音频")
                            )
                    elif event_type == "session.finished":
                        break
                    elif event_type == "error":
                        error = payload.get("error", payload)
                        raise RuntimeError(
                            "DashScope 实时 TTS 返回错误: "
                            + json.dumps(error, ensure_ascii=False)
                        )
                elif message.type in {
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                    WSMsgType.ERROR,
                }:
                    raise RuntimeError(
                        f"DashScope 实时 TTS WebSocket 异常关闭: {message.type}"
                    )
            if not self.closed and not session_ready:
                raise RuntimeError("DashScope 实时 TTS WebSocket 在就绪前关闭")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.state_lock:
                ready_future = self.ready_future
                first_audio = self.first_audio_future
            if ready_future is not None and not ready_future.done():
                ready_future.set_exception(exc)
            if first_audio is not None and not first_audio.done():
                first_audio.set_exception(exc)
            raise
        finally:
            should_rewarm = False
            async with self.state_lock:
                if generation == self.generation:
                    self.websocket = None
                    self.receiver_task = None
                    self.response_active = False
                    self.status.tts_connected = False
                    should_rewarm = session_ready and not self.closed
            if should_rewarm:
                warm_task = asyncio.create_task(self._warm_after_interrupt())
                warm_task.add_done_callback(self._consume_task_result)
                self.warm_task = warm_task

    async def _ensure_connected(self) -> None:
        if self.closed:
            raise RuntimeError("DashScope 实时 TTS 客户端已关闭")
        async with self.connection_lock:
            async with self.state_lock:
                ready_future = self.ready_future
                connected = bool(
                    self.websocket is not None
                    and not self.websocket.closed
                    and self.receiver_task is not None
                    and not self.receiver_task.done()
                    and self.playback_process is not None
                    and self.playback_process.returncode is None
                    and ready_future is not None
                    and ready_future.done()
                    and not ready_future.cancelled()
                    and ready_future.exception() is None
                )
            if connected:
                return
            await self._disconnect_locked()
            process = await self._spawn_playback_process()
            try:
                websocket = await self.session.ws_connect(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "User-Agent": "maibot-qq-voice-call/0.1",
                    },
                    timeout=ClientWSTimeout(ws_receive=None, ws_close=5),
                    heartbeat=20,
                )
            except Exception:
                await self._terminate_process(process)
                raise

            ready_future = asyncio.get_running_loop().create_future()
            async with self.state_lock:
                self.generation += 1
                generation = self.generation
                self.playback_process = process
                self.websocket = websocket
                self.ready_future = ready_future
                task = asyncio.create_task(
                    self._receive_events(websocket, process, generation)
                )
                task.add_done_callback(self._consume_task_result)
                self.receiver_task = task
            try:
                await asyncio.wait_for(asyncio.shield(ready_future), timeout=8)
            except BaseException:
                await self._disconnect_locked()
                raise

    async def _warm_after_interrupt(self) -> None:
        try:
            await self._ensure_connected()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("DashScope 实时 TTS 重新预热失败: %s", exc)

    async def startup(self) -> None:
        started_at = time.perf_counter()
        await self._ensure_connected()
        self.logger.info(
            "DashScope 实时 TTS 会话预热完成，耗时 %.3f 秒",
            time.perf_counter() - started_at,
        )

    async def stop(self) -> bool:
        async with self.state_lock:
            was_playing = bool(
                self.response_active or time.monotonic() < self.playback_until + 0.03
            )
        if not was_playing:
            return False
        async with self.connection_lock:
            await self._disconnect_locked(interrupted=True)
        if not self.closed:
            warm_task = asyncio.create_task(self._warm_after_interrupt())
            warm_task.add_done_callback(self._consume_task_result)
            self.warm_task = warm_task
        return True

    async def start(self, text: str) -> float:
        async with self.turn_lock:
            async with self.state_lock:
                active = self.response_active
            if active:
                await self.stop()
            started_at = time.perf_counter()
            await self._ensure_connected()
            first_audio = asyncio.get_running_loop().create_future()
            async with self.state_lock:
                websocket = self.websocket
                if websocket is None or websocket.closed:
                    raise RuntimeError("DashScope 实时 TTS WebSocket 尚未就绪")
                self.first_audio_future = first_audio
                self.response_active = True
                self.response_started_at = started_at
                self.playback_until = time.monotonic()
            try:
                await websocket.send_json(
                    self.event("input_text_buffer.append", text=text)
                )
                await websocket.send_json(self.event("input_text_buffer.commit"))
                return await asyncio.wait_for(asyncio.shield(first_audio), timeout=15)
            except BaseException:
                async with self.connection_lock:
                    await self._disconnect_locked()
                raise

    async def close(self) -> None:
        self.closed = True
        warm_task = self.warm_task
        self.warm_task = None
        if warm_task is not None and not warm_task.done():
            warm_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warm_task
        async with self.connection_lock:
            await self._disconnect_locked()
