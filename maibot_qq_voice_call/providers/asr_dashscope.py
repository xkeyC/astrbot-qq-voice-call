"""Pre-warmed manual-mode Qwen realtime ASR connection."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
import uuid
from collections import deque
from typing import Any

from aiohttp import ClientSession, ClientWSTimeout, WSMsgType

from ..config import ASRSection, AudioSection
from ..models import RuntimeStatus


def _model_url(base_url: str, model: str) -> str:
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}model={model}"


class DashScopeRealtimeASR:
    def __init__(
        self,
        session: ClientSession,
        config: ASRSection,
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
        if not self.api_key:
            raise RuntimeError(f"实时 ASR API Key 为空: {config.api_key_env}")
        self.url = _model_url(config.websocket_base_url, config.model)
        self.state_lock = asyncio.Lock()
        self.connection_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.websocket: Any | None = None
        self.receiver_task: asyncio.Task[None] | None = None
        self.ready_future: asyncio.Future[None] | None = None
        self.pending_commits: deque[asyncio.Future[tuple[str, float]]] = deque()
        self.pending_by_item: dict[str, asyncio.Future[tuple[str, float]]] = {}
        self.pending_results: deque[asyncio.Future[tuple[str, float]]] = deque()
        self.commit_started: dict[asyncio.Future[tuple[str, float]], float] = {}
        self.buffer_active = False
        self.generation = 0
        self.closed = False

    @staticmethod
    def event(event_type: str, **payload: object) -> dict[str, object]:
        return {
            "event_id": f"event_{uuid.uuid4().hex}",
            "type": event_type,
            **payload,
        }

    def _consume_task_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self.logger.warning("Qwen 实时 ASR 后台连接失败: %s", exception)

    def _fail_pending(self, exception: Exception) -> None:
        futures = [
            *self.pending_commits,
            *self.pending_by_item.values(),
            *self.pending_results,
        ]
        self.pending_commits.clear()
        self.pending_by_item.clear()
        self.pending_results.clear()
        self.commit_started.clear()
        for future in futures:
            if not future.done():
                future.set_exception(exception)

    async def _disconnect_locked(self) -> None:
        async with self.state_lock:
            self.generation += 1
            websocket = self.websocket
            task = self.receiver_task
            ready_future = self.ready_future
            self.websocket = None
            self.receiver_task = None
            self.ready_future = None
            self.buffer_active = False
            self.status.asr_connected = False
            self._fail_pending(RuntimeError("Qwen 实时 ASR 连接已重置"))
        if ready_future is not None and not ready_future.done():
            ready_future.cancel()
        if websocket is not None and not websocket.closed:
            with contextlib.suppress(Exception):
                await websocket.close()
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _disconnect(self) -> None:
        async with self.connection_lock:
            await self._disconnect_locked()

    async def _receive_events(self, websocket: Any, generation: int) -> None:
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
                                    "modalities": ["text"],
                                    "input_audio_format": "pcm",
                                    "sample_rate": self.audio.sample_rate,
                                    "input_audio_transcription": {
                                        "language": "zh",
                                        "corpus": {"text": "MaiBot NapCat QQ"},
                                    },
                                    "turn_detection": None,
                                },
                            )
                        )
                    elif event_type == "session.updated":
                        session_ready = True
                        async with self.state_lock:
                            ready_future = self.ready_future
                            self.status.asr_connected = True
                        if ready_future is not None and not ready_future.done():
                            ready_future.set_result(None)
                    elif event_type == "input_audio_buffer.committed":
                        item_id = str(payload.get("item_id") or "")
                        async with self.state_lock:
                            future = (
                                self.pending_commits.popleft()
                                if self.pending_commits
                                else None
                            )
                            if future is not None and item_id:
                                self.pending_by_item[item_id] = future
                            elif future is not None:
                                self.pending_results.append(future)
                    elif event_type == (
                        "conversation.item.input_audio_transcription.completed"
                    ):
                        item_id = str(payload.get("item_id") or "")
                        transcript = str(payload.get("transcript") or "").strip()
                        async with self.state_lock:
                            future = (
                                self.pending_by_item.pop(item_id, None)
                                if item_id
                                else (
                                    self.pending_results.popleft()
                                    if self.pending_results
                                    else None
                                )
                            )
                            started_at = (
                                self.commit_started.pop(future, 0.0)
                                if future is not None
                                else 0.0
                            )
                        if future is not None and not future.done():
                            future.set_result(
                                (
                                    transcript,
                                    time.perf_counter() - started_at
                                    if started_at
                                    else 0.0,
                                )
                            )
                    elif event_type == (
                        "conversation.item.input_audio_transcription.failed"
                    ):
                        item_id = str(payload.get("item_id") or "")
                        error = payload.get("error", payload)
                        async with self.state_lock:
                            future = (
                                self.pending_by_item.pop(item_id, None)
                                if item_id
                                else (
                                    self.pending_results.popleft()
                                    if self.pending_results
                                    else None
                                )
                            )
                            if future is not None:
                                self.commit_started.pop(future, None)
                        if future is not None and not future.done():
                            future.set_exception(
                                RuntimeError(
                                    "Qwen 实时 ASR 转录失败: "
                                    + json.dumps(error, ensure_ascii=False)
                                )
                            )
                    elif event_type == "error":
                        error = payload.get("error", payload)
                        raise RuntimeError(
                            "Qwen 实时 ASR 返回错误: "
                            + json.dumps(error, ensure_ascii=False)
                        )
                    elif event_type == "session.finished":
                        break
                elif message.type in {
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                    WSMsgType.ERROR,
                }:
                    raise RuntimeError(
                        f"Qwen 实时 ASR WebSocket 异常关闭: {message.type}"
                    )
            if not self.closed and not session_ready:
                raise RuntimeError("Qwen 实时 ASR WebSocket 在就绪前关闭")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self.state_lock:
                ready_future = self.ready_future
                if generation == self.generation:
                    self.status.asr_connected = False
                    self._fail_pending(exc)
            if ready_future is not None and not ready_future.done():
                ready_future.set_exception(exc)
            raise
        finally:
            should_rewarm = False
            async with self.state_lock:
                if generation == self.generation:
                    self.websocket = None
                    self.receiver_task = None
                    self.buffer_active = False
                    self.status.asr_connected = False
                    should_rewarm = session_ready and not self.closed
            if should_rewarm:
                task = asyncio.create_task(self._warm_after_disconnect())
                task.add_done_callback(self._consume_task_result)

    async def _ensure_connected(self) -> None:
        if self.closed:
            raise RuntimeError("Qwen 实时 ASR 客户端已关闭")
        async with self.connection_lock:
            async with self.state_lock:
                ready_future = self.ready_future
                connected = bool(
                    self.websocket is not None
                    and not self.websocket.closed
                    and self.receiver_task is not None
                    and not self.receiver_task.done()
                    and ready_future is not None
                    and ready_future.done()
                    and not ready_future.cancelled()
                    and ready_future.exception() is None
                )
            if connected:
                return
            await self._disconnect_locked()
            websocket = await self.session.ws_connect(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "OpenAI-Beta": "realtime=v1",
                    "User-Agent": "maibot-qq-voice-call/0.1",
                },
                timeout=ClientWSTimeout(ws_receive=None, ws_close=5),
                heartbeat=20,
            )
            ready_future = asyncio.get_running_loop().create_future()
            async with self.state_lock:
                self.generation += 1
                generation = self.generation
                self.websocket = websocket
                self.ready_future = ready_future
                task = asyncio.create_task(self._receive_events(websocket, generation))
                task.add_done_callback(self._consume_task_result)
                self.receiver_task = task
            try:
                await asyncio.wait_for(asyncio.shield(ready_future), timeout=8)
            except BaseException:
                await self._disconnect_locked()
                raise

    async def startup(self) -> None:
        started_at = time.perf_counter()
        await self._ensure_connected()
        self.logger.info(
            "Qwen 实时 ASR 会话预热完成，耗时 %.3f 秒",
            time.perf_counter() - started_at,
        )

    async def _warm_after_disconnect(self) -> None:
        try:
            await self._ensure_connected()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("Qwen 实时 ASR 自动重新预热失败: %s", exc)

    async def begin(self, frames: list[bytes]) -> bool:
        try:
            await self._ensure_connected()
            async with self.send_lock:
                async with self.state_lock:
                    websocket = self.websocket
                    buffer_active = self.buffer_active
                if websocket is None or websocket.closed or buffer_active:
                    raise RuntimeError("Qwen 实时 ASR 输入缓冲区状态异常")
                await websocket.send_json(
                    self.event(
                        "input_audio_buffer.append",
                        audio=base64.b64encode(b"".join(frames)).decode("ascii"),
                    )
                )
                async with self.state_lock:
                    self.buffer_active = True
            return True
        except Exception as exc:
            self.logger.warning("Qwen 实时 ASR 开始流式输入失败，回退 MaiBot ASR: %s", exc)
            await self._disconnect()
            return False

    async def append(self, frame: bytes) -> bool:
        try:
            async with self.send_lock:
                async with self.state_lock:
                    websocket = self.websocket
                    buffer_active = self.buffer_active
                if websocket is None or websocket.closed or not buffer_active:
                    return False
                await websocket.send_json(
                    self.event(
                        "input_audio_buffer.append",
                        audio=base64.b64encode(frame).decode("ascii"),
                    )
                )
            return True
        except Exception as exc:
            self.logger.warning("Qwen 实时 ASR 流式输入中断，回退 MaiBot ASR: %s", exc)
            await self._disconnect()
            return False

    async def commit(self) -> asyncio.Future[tuple[str, float]] | None:
        future: asyncio.Future[tuple[str, float]] | None = None
        send_error: Exception | None = None
        async with self.send_lock:
            async with self.state_lock:
                websocket = self.websocket
                buffer_active = self.buffer_active
            if websocket is None or websocket.closed or not buffer_active:
                return None
            future = asyncio.get_running_loop().create_future()
            async with self.state_lock:
                self.pending_commits.append(future)
                self.commit_started[future] = time.perf_counter()
                self.buffer_active = False
            try:
                await websocket.send_json(self.event("input_audio_buffer.commit"))
            except Exception as exc:
                send_error = exc
                if not future.done():
                    future.set_exception(exc)
        if send_error is not None:
            await self._disconnect()
        return future

    async def abort_buffer(self) -> None:
        async with self.state_lock:
            buffer_active = self.buffer_active
        if buffer_active:
            await self._disconnect()

    async def close(self) -> None:
        self.closed = True
        async with self.connection_lock:
            websocket = self.websocket
            if websocket is not None and not websocket.closed:
                with contextlib.suppress(Exception):
                    await websocket.send_json(self.event("session.finish"))
            await self._disconnect_locked()
