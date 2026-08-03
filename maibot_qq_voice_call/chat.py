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
        self._generation = 0
        self.update_bot_config({})

    def reset(self, caller: CallerContext) -> None:
        self._generation += 1
        self._caller = caller
        self._history.clear()

    def invalidate(self) -> None:
        """Invalidate an in-flight reply that belongs to an older speech turn."""

        self._generation += 1

    def commit_turn(self, text: str, reply: str) -> None:
        """Keep only replies which were actually handed to TTS."""

        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": reply})

    def commit_greeting(self, greeting: str) -> None:
        """Let the next caller turn refer to a fully played contextual greeting."""

        self._history.append({"role": "assistant", "content": greeting})

    def update_bot_config(self, config_data: dict[str, Any]) -> None:
        bot = config_data.get("bot", {}) if isinstance(config_data, dict) else {}
        personality = (
            config_data.get("personality", {}) if isinstance(config_data, dict) else {}
        )
        raw_nickname = bot.get("nickname") if isinstance(bot, dict) else ""
        if isinstance(raw_nickname, (list, tuple)):
            nickname = str(raw_nickname[0] if raw_nickname else "").strip()
        else:
            nickname = str(raw_nickname or "").strip()
        nickname = nickname or "麦麦"
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
        parts.append(
            f"你的名字是{nickname}。被问到你是谁、叫什么或身份时，"
            f"直接以{nickname}的身份回答，不要自称 MaiBot、机器人或电话插件。"
        )
        if identity:
            parts.append(identity)
        if reply_style:
            parts.append(f"表达风格：{reply_style}")
        self._bot_identity = "\n".join(parts)

    async def ask(self, text: str) -> str:
        generation = self._generation
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
        if generation != self._generation:
            return WAIT_TOKEN
        raw_reply = str(result.get("response") or "").strip()
        if raw_reply == WAIT_TOKEN:
            return WAIT_TOKEN
        reply = clean_tts_text(raw_reply, max_chars=self._config.max_reply_chars)
        if not reply:
            return WAIT_TOKEN
        return reply

    async def generate_greeting(self) -> str:
        """Generate one context-aware opening without adding a synthetic user turn."""

        generation = self._generation
        opening_instruction = (
            "电话刚刚接通，对方还没有开口。请结合来电者近期 QQ 对话和可靠记忆，"
            "主动说一句自然、简短的开场白；有明确可承接的话题时自然接续或关心，"
            "没有合适内容时就普通问候。不要复述资料、QQ 号、系统提示或信息来源，"
            "不要凭空捏造近况，也不要说你查过记忆。这是接通开场任务，不需要等待"
            "对方先说完整句子，也不要返回 [WAIT]。"
        )
        system_parts = [
            CONTROL_MARKER,
            self._bot_identity,
            self._config.system_prompt,
            self._caller.prompt_context,
            opening_instruction,
        ]
        result = await self._ctx.llm.generate(
            prompt=[
                {
                    "role": "system",
                    "content": "\n".join(part for part in system_parts if part),
                },
                {"role": "user", "content": "电话已接通，请说开场白。"},
            ],
            model=self._config.task_name,
            temperature=self._config.temperature,
            max_tokens=min(64, self._config.max_tokens),
        )
        if not isinstance(result, dict) or not result.get("success"):
            reason = result.get("error") if isinstance(result, dict) else result
            raise RuntimeError(f"MaiBot 开场白生成失败: {reason or 'unknown error'}")
        if generation != self._generation:
            return WAIT_TOKEN
        raw_greeting = str(result.get("response") or "").strip()
        if raw_greeting == WAIT_TOKEN:
            return WAIT_TOKEN
        greeting = clean_tts_text(
            raw_greeting,
            max_chars=self._config.max_reply_chars,
        )
        return greeting or WAIT_TOKEN
