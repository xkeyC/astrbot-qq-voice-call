"""Planner-free phone replies through MaiBot's configured LLM capability."""

from __future__ import annotations

from collections import deque
from typing import Any

from maibot_sdk import PluginContext

from .config import ChatSection
from .models import CallerContext
from .text import CONTROL_MARKER, WAIT_TOKEN, clean_tts_text


class MaiBotPhoneChat:
    def __init__(self, ctx: PluginContext, config: ChatSection) -> None:
        self._ctx = ctx
        self._config = config
        self._caller = CallerContext()
        self._history: deque[dict[str, str]] = deque(maxlen=config.history_messages)
        self._bot_identity = ""

    def reset(self, caller: CallerContext) -> None:
        self._caller = caller
        self._history.clear()

    def update_bot_config(self, config_data: dict[str, Any]) -> None:
        bot = config_data.get("bot", {}) if isinstance(config_data, dict) else {}
        personality = (
            config_data.get("personality", {}) if isinstance(config_data, dict) else {}
        )
        nickname = str(bot.get("nickname") or "").strip() if isinstance(bot, dict) else ""
        identity = (
            str(personality.get("personality") or "").strip()
            if isinstance(personality, dict)
            else ""
        )
        reply_style = (
            str(personality.get("reply_style") or "").strip()
            if isinstance(personality, dict)
            else ""
        )
        parts = []
        if nickname:
            parts.append(f"你的名字是{nickname}。")
        if identity:
            parts.append(identity)
        if reply_style:
            parts.append(f"表达风格：{reply_style}")
        self._bot_identity = "\n".join(parts)

    async def ask(self, text: str) -> str:
        system_parts = [CONTROL_MARKER, self._bot_identity, self._config.system_prompt]
        if self._caller.prompt_context:
            system_parts.append(self._caller.prompt_context)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": "\n".join(part for part in system_parts if part),
            },
            *self._history,
            {"role": "user", "content": text},
        ]
        result = await self._ctx.llm.generate(
            prompt=messages,
            model=self._config.task_name,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        if not isinstance(result, dict) or not result.get("success"):
            reason = result.get("error") if isinstance(result, dict) else result
            raise RuntimeError(f"MaiBot LLM 调用失败: {reason or 'unknown error'}")
        raw_reply = str(result.get("response") or "").strip()
        if raw_reply == WAIT_TOKEN:
            return WAIT_TOKEN
        reply = clean_tts_text(raw_reply, max_chars=self._config.max_reply_chars)
        if not reply:
            return WAIT_TOKEN
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": reply})
        return reply
