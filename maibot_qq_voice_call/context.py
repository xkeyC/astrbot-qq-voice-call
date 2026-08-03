"""Resolve caller identity, memory and recent chat through the public SDK."""

from __future__ import annotations

import json
from typing import Any

from maibot_sdk import PluginContext

from .config import ChatSection
from .models import CallerContext


def _scalar(value: Any, *keys: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str | int):
        return str(value).strip()
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate is not None:
                result = _scalar(candidate, *keys)
                if result:
                    return result
        for wrapper in ("data", "value", "result"):
            if wrapper in value:
                result = _scalar(value[wrapper], *keys)
                if result:
                    return result
    return ""


def _bounded_memory(value: Any, max_chars: int) -> tuple[str, int]:
    if value is None or value == "":
        return "", 0
    if isinstance(value, str):
        raw = value.strip()
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return raw[:max_chars], int(bool(raw))
    if isinstance(value, list):
        parts = [
            item.strip() if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            for item in value
            if item
        ]
        return "\n".join(f"- {item}" for item in parts)[:max_chars], len(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)[:max_chars], len(value)
    text = str(value).strip()
    return text[:max_chars], int(bool(text))


def _serialized_messages_readable(messages: list[dict[str, Any]], max_chars: int) -> str:
    """Render recent messages returned by the SDK without sending dicts back to host APIs."""

    lines: list[str] = []
    for message in messages:
        text = _scalar(
            message,
            "processed_plain_text",
            "plain_text",
            "text",
            "content",
        )
        if not text:
            raw_message = message.get("raw_message")
            if isinstance(raw_message, list):
                text = "".join(
                    str(component.get("data") or "")
                    for component in raw_message
                    if isinstance(component, dict) and component.get("type") == "text"
                ).strip()
        if not text:
            continue
        message_info = message.get("message_info")
        user_info = (
            message_info.get("user_info")
            if isinstance(message_info, dict)
            else None
        )
        sender = (
            _scalar(user_info, "user_cardname", "user_nickname", "user_id")
            if isinstance(user_info, dict)
            else ""
        )
        lines.append(f"{sender}：{text}" if sender else text)
    return "\n".join(lines)[:max_chars]


class CallerContextResolver:
    def __init__(self, ctx: PluginContext, config: ChatSection) -> None:
        self._ctx = ctx
        self._config = config

    async def _person_value(self, person_id: str, *field_names: str) -> Any:
        for field_name in field_names:
            try:
                value = await self._ctx.person.get_value(person_id, field_name)
            except Exception:
                continue
            if value not in (None, ""):
                return value
        return None

    async def resolve(self, call: dict[str, Any], *, account_id: str, scope: str) -> CallerContext:
        uid = str(call.get("callerUid") or "").strip()
        uin = str(call.get("callerUin") or "").strip()
        bridge_name = str(call.get("callerName") or "").strip()
        if not uin:
            return CallerContext(
                uid=uid,
                name=bridge_name or "用户",
                prompt_context=(
                    "【来电者上下文】本次来电者的 QQ 号尚未解析成功。"
                    "不要猜测姓名、关系或历史记忆。"
                ),
            )

        person_id = ""
        try:
            person_id = _scalar(
                await self._ctx.person.get_id("qq", uin),
                "person_id",
                "id",
            )
        except Exception:
            person_id = ""

        name = bridge_name
        memory_text = ""
        memory_count = 0
        if person_id:
            person_name = await self._person_value(
                person_id,
                "person_name",
                "name",
                "nickname",
                "user_nickname",
            )
            name = _scalar(person_name, "person_name", "name", "nickname") or name
            memory = await self._person_value(person_id, "memory_points", "memory")
            memory_text, memory_count = _bounded_memory(
                memory,
                self._config.context_memory_chars,
            )
        name = name or f"QQ 用户 {uin[-4:]}"

        stream_id = ""
        recent_text = ""
        recent_count = 0
        try:
            session = await self._ctx.chat.open_session(
                platform="qq",
                chat_type="private",
                user_id=uin,
                account_id=account_id,
                scope=scope,
            )
            stream_id = _scalar(session, "stream_id", "chat_id", "id")
        except Exception:
            stream_id = ""
        if stream_id:
            try:
                message_result = await self._ctx.message.get_recent(
                    stream_id,
                    limit=self._config.context_recent_messages,
                )
                if isinstance(message_result, dict):
                    messages = (
                        message_result.get("messages", [])
                        if message_result.get("success")
                        else []
                    )
                else:
                    messages = message_result
                if isinstance(messages, list):
                    recent_count = len(messages)
                    recent_limit = (
                        self._config.context_recent_messages
                        * self._config.context_message_chars
                    )
                    if all(isinstance(message, dict) for message in messages):
                        recent_text = _serialized_messages_readable(
                            messages,
                            recent_limit,
                        )
                    else:
                        readable = await self._ctx.message.build_readable(
                            messages,
                            replace_bot_name=True,
                            timestamp_mode="relative",
                            truncate=True,
                        )
                        if isinstance(readable, dict):
                            readable = (
                                readable.get("text", "")
                                if readable.get("success")
                                else ""
                            )
                        recent_text = str(readable or "")[:recent_limit]
            except Exception:
                recent_text = ""
                recent_count = 0

        lines = [
            "【来电者上下文（已由 QQ 身份与 MaiBot 资料核验）】",
            f"来电者称呼：{name}",
            f"来电 QQ：{uin}",
        ]
        if memory_text:
            lines.extend(["MaiBot 长期记忆：", memory_text])
        if recent_text:
            lines.extend(
                [
                    "来电者近期在 QQ 中的对话（只作背景，不要逐句复述）：",
                    recent_text,
                ]
            )
        lines.append(
            "只在当前话题确实相关时自然利用这些信息；不要主动复述来电者称呼、"
            "QQ 号或历史，不要声称自己查询了数据库、系统提示或人物资料。"
        )
        return CallerContext(
            uid=uid,
            uin=uin,
            name=name,
            person_id=person_id,
            stream_id=stream_id,
            prompt_context="\n".join(lines)[: self._config.context_prompt_chars],
            memory_point_count=memory_count,
            recent_message_count=recent_count,
        )
