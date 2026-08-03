"""MaiBot plugin entry point."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

from maibot_sdk import (
    API,
    CONFIG_RELOAD_SCOPE_SELF,
    ON_BOT_CONFIG_RELOAD,
    Command,
    MaiBotPlugin,
    MessageGateway,
)

# MaiBot loads plugin.py through an isolated importlib spec and does not add the
# plugin directory to sys.path. Add only this repository root so the bundled
# runtime package remains importable without requiring a separate pip install.
_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from maibot_qq_voice_call.config import QQVoiceCallConfig  # noqa: E402
from maibot_qq_voice_call.constants import CALL_ARCHIVE_PREFIX, GATEWAY_NAME  # noqa: E402
from maibot_qq_voice_call.orchestrator import CallOrchestrator  # noqa: E402


class QQVoiceCallPlugin(MaiBotPlugin):
    """Low-latency QQ calls backed by MaiBot context and model routing."""

    config_model = QQVoiceCallConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = ("bot",)

    async def on_load(self) -> None:
        self._runtime: CallOrchestrator | None = None
        self._runtime_task: asyncio.Task[None] | None = None
        await self._update_gateway_state(False)
        if self.config.plugin.enabled:
            await self._start_runtime()
        else:
            self.ctx.logger.info("QQ 语音通话插件已加载，但尚未启用")

    async def on_unload(self) -> None:
        await self._stop_runtime()
        await self._update_gateway_state(False)

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        if scope == CONFIG_RELOAD_SCOPE_SELF:
            self.ctx.logger.info("QQ 通话配置已更新，重启运行时: version=%s", version)
            await self._stop_runtime()
            if self.config.plugin.enabled:
                await self._start_runtime()
            else:
                await self._update_gateway_state(False)
        elif scope == ON_BOT_CONFIG_RELOAD and self._runtime is not None:
            self._runtime.chat.update_bot_config(config_data)

    async def _update_gateway_state(self, ready: bool) -> None:
        await self.ctx.gateway.update_state(
            gateway_name=GATEWAY_NAME,
            ready=ready,
            platform="qq",
            account_id=self.config.plugin.account_id,
            scope=self.config.plugin.scope,
            metadata={
                "protocol": "napcat-av-bridge",
                "media": "voice-call",
            },
        )

    async def _start_runtime(self) -> None:
        if self._runtime_task is not None and not self._runtime_task.done():
            return
        runtime = CallOrchestrator(self.ctx, self.config, self.ctx.logger)
        self._runtime = runtime
        task = asyncio.create_task(
            runtime.run(ready_callback=self._update_gateway_state),
            name="maibot-qq-voice-call",
        )
        task.add_done_callback(self._runtime_done)
        self._runtime_task = task

    def _runtime_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self.ctx.logger.error(
                "QQ 语音通话运行时已退出: %s",
                exception,
                exc_info=(type(exception), exception, exception.__traceback__),
            )

    async def _stop_runtime(self) -> None:
        runtime = self._runtime
        task = self._runtime_task
        self._runtime = None
        self._runtime_task = None
        if runtime is not None:
            runtime.stop_event.set()
        if task is not None and not task.done():
            memory_timeout = (
                runtime.config.memory.write_timeout_seconds
                if runtime is not None
                else self.config.memory.write_timeout_seconds
            )
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=max(10.0, memory_timeout + 2.0),
                )
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @MessageGateway(
        route_type="receive",
        name=GATEWAY_NAME,
        description="QQ voice-call state and audio gateway",
        platform="qq",
        protocol="napcat-av-bridge",
        scope="primary",
    )
    async def handle_gateway(
        self,
        message: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        # Receive-only gateways are never selected for MaiBot outbound messages.
        return {"success": True}

    @Command(
        "qq_voice_call_archive",
        pattern=r"^\[QQ语音通话记录\]",
        description="内部通话记忆归档入口",
    )
    async def handle_call_archive(self, **kwargs: Any) -> tuple[bool, None, int]:
        """Persist the synthetic call record without sending a text reply."""

        message_text = str(
            kwargs.get("processed_plain_text")
            or kwargs.get("message_text")
            or kwargs.get("text")
            or ""
        )
        if message_text and not message_text.startswith(CALL_ARCHIVE_PREFIX):
            return False, None, 0
        return True, None, 1

    @API(
        "get_call_status",
        description="获取 QQ 语音通话状态、链路耗时和最近一次记忆写回结果",
        version="1",
        public=True,
    )
    async def get_call_status(self, **kwargs: Any) -> dict[str, Any]:
        if self._runtime is None:
            return {
                "success": True,
                "enabled": self.config.plugin.enabled,
                "ready": False,
            }
        return {
            "success": True,
            "enabled": self.config.plugin.enabled,
            **self._runtime.status.to_dict(),
        }

    @API(
        "test_phone_reply",
        description="测试电话模式 LLM 回复；仅显式要求时才向当前电话播放",
        version="1",
        public=False,
    )
    async def test_phone_reply(
        self,
        text: str,
        speak: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if self._runtime is None or not self._runtime.status.ready:
            return {"success": False, "error": "QQ 通话运行时尚未就绪"}
        return await self._runtime.manual_chat(text, speak=speak)


def create_plugin() -> QQVoiceCallPlugin:
    return QQVoiceCallPlugin()
