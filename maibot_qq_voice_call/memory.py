"""Post-call transcript cleanup, summarization and MaiBot memory writeback."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from maibot_sdk import PluginContext

from .config import MemorySection
from .constants import CALL_ARCHIVE_PREFIX, CALL_ARCHIVE_SOURCE, GATEWAY_NAME
from .models import CallerContext
from .text import (
    CONTROL_MARKER,
    WAIT_TOKEN,
    is_filler_transcript,
    is_incomplete_transcript,
    normalize_turn_text,
)


@dataclass(frozen=True, slots=True)
class CallTurn:
    """One confirmed caller/assistant turn from a connected call."""

    caller_text: str
    assistant_text: str
    timestamp: float


@dataclass(frozen=True, slots=True)
class CallArchive:
    """Immutable snapshot captured when a call disconnects."""

    invite_at: str
    caller: CallerContext
    turns: tuple[CallTurn, ...]
    started_at: float
    ended_at: float
    account_id: str
    scope: str


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    """Observable result of one post-call memory write."""

    success: bool
    turn_count: int = 0
    summary: str = ""
    facts: tuple[str, ...] = ()
    persisted: bool = False
    context_appended: bool = False
    skipped_reason: str = ""


def _clean_line(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[: max(0, max_chars)].strip()


def sanitize_call_turns(
    turns: tuple[CallTurn, ...] | list[CallTurn],
    *,
    max_turns: int,
) -> list[CallTurn]:
    """Drop filler, unfinished, duplicate and control-content turns."""

    if max_turns <= 0:
        return []
    cleaned: list[CallTurn] = []
    previous_key = ""
    for turn in turns:
        caller_text = _clean_line(turn.caller_text, 500)
        assistant_text = _clean_line(turn.assistant_text, 500)
        if (
            not caller_text
            or is_filler_transcript(caller_text)
            or is_incomplete_transcript(caller_text)
            or not assistant_text
            or assistant_text == WAIT_TOKEN
            or CONTROL_MARKER in assistant_text
        ):
            continue
        key = f"{caller_text.casefold()}\n{assistant_text.casefold()}"
        if key == previous_key:
            continue
        previous_key = key
        cleaned.append(
            CallTurn(
                caller_text=caller_text,
                assistant_text=assistant_text,
                timestamp=turn.timestamp,
            )
        )
    if len(cleaned) <= max_turns:
        return cleaned
    head_count = max_turns // 2
    return [*cleaned[:head_count], *cleaned[-(max_turns - head_count) :]]


def format_call_transcript(turns: list[CallTurn], *, max_chars: int) -> str:
    """Format confirmed turns while preserving complete role pairs."""

    if max_chars <= 0:
        return ""
    lines: list[str] = []
    used_chars = 0
    for turn in turns:
        pair = f"对方：{turn.caller_text}\n麦麦：{turn.assistant_text}"
        added_chars = len(pair) + (2 if lines else 0)
        if lines and used_chars + added_chars > max_chars:
            break
        lines.append(pair)
        used_chars += added_chars
    return "\n\n".join(lines)[:max_chars]


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = str(text or "").strip()
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(candidate[start : end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_summary_payload(
    raw_text: str,
    *,
    max_summary_chars: int,
    max_facts: int,
    caller_texts: list[str] | None = None,
) -> tuple[str, tuple[str, ...]]:
    payload = _extract_json_object(raw_text)
    summary = _clean_line(payload.get("summary", ""), max_summary_chars)
    raw_facts = payload.get("facts", [])
    facts: list[str] = []
    normalized_caller_texts = [normalize_turn_text(text) for text in caller_texts or []]
    if isinstance(raw_facts, list) and max_facts > 0:
        for item in raw_facts:
            evidence = ""
            if isinstance(item, dict):
                fact = _clean_line(item.get("fact", ""), 100)
                evidence = _clean_line(item.get("evidence", ""), 120)
            else:
                fact = _clean_line(item, 100)
            if caller_texts is not None:
                normalized_evidence = normalize_turn_text(evidence)
                if not normalized_evidence or not any(
                    normalized_evidence in caller_text
                    for caller_text in normalized_caller_texts
                ):
                    continue
                normalized_fact = normalize_turn_text(fact)
                if len(normalized_evidence) < 3 or not any(
                    normalized_evidence[index : index + 3] in normalized_fact
                    for index in range(len(normalized_evidence) - 2)
                ):
                    continue
                fact = f"{fact}（原话：“{evidence}”）"
            if fact and fact not in facts:
                facts.append(fact)
            if len(facts) >= max_facts:
                break
    return summary, tuple(facts)


def _fallback_summary(turns: list[CallTurn], max_chars: int) -> str:
    caller_points: list[str] = []
    for turn in turns:
        if turn.caller_text not in caller_points:
            caller_points.append(turn.caller_text)
        if len(caller_points) >= 4:
            break
    text = "；".join(caller_points)
    return _clean_line(f"本次电话中，对方主要提到：{text}", max_chars)


def _extract_stream_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("stream_id", "chat_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    for key in ("stream", "data", "result"):
        nested = _extract_stream_id(value.get(key))
        if nested:
            return nested
    return ""


def _build_message_id(archive: CallArchive) -> str:
    material = (
        f"{archive.invite_at}|{archive.caller.uin}|"
        f"{archive.started_at:.6f}|{archive.ended_at:.6f}"
    )
    digest = sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"qq-call-memory-{digest}"


def _format_archive_record(
    archive: CallArchive,
    transcript: str,
    summary: str,
    facts: tuple[str, ...],
    *,
    include_transcript: bool,
) -> str:
    started = datetime.fromtimestamp(archive.started_at).astimezone().strftime("%Y-%m-%d %H:%M")
    duration = max(0, round(archive.ended_at - archive.started_at))
    lines = [
        CALL_ARCHIVE_PREFIX,
        f"通话时间：{started}，约 {duration} 秒",
        f"通话摘要：{summary}",
    ]
    if facts:
        lines.append("关键人物事实（仅来自对方明确表达）：")
        lines.extend(f"- {fact}" for fact in facts)
    else:
        lines.append("关键人物事实：本次没有提取到可确认的长期事实。")
    if include_transcript:
        lines.extend(
            [
                "有效对话（“麦麦”发言不能作为对方人物事实）：",
                transcript,
            ]
        )
    return "\n".join(lines).strip()


class CallMemoryWriter:
    """Summarize a completed call and feed it back through public MaiBot APIs."""

    def __init__(
        self,
        ctx: PluginContext,
        config: MemorySection,
        logger,
    ) -> None:
        self._ctx = ctx
        self._config = config
        self._logger = logger

    async def _summarize(
        self,
        caller: CallerContext,
        turns: list[CallTurn],
        transcript: str,
    ) -> tuple[str, tuple[str, ...]]:
        prompt = [
            {
                "role": "system",
                "content": (
                    "你负责整理 QQ 语音通话记忆。只依据标为“对方”的发言，"
                    "不要把麦麦的回复、猜测、语气词或未说完片段写成人物事实。"
                    "summary 用一到三句概括有效话题；facts 只保留对方明确表达且未来"
                    "有帮助的身份、偏好、经历、计划或承诺，没有则为空数组。"
                    "fact 必须忠实改写并保留至少三个连续的原话文字，每条事实都要"
                    "附带对方发言中的逐字证据；只输出 JSON："
                    "{\"summary\":\"...\",\"facts\":[{\"fact\":\"...\","
                    "\"evidence\":\"对方原话片段\"}]}。"
                ),
            },
            {
                "role": "user",
                "content": f"来电者称呼：{caller.name}\n\n有效对话：\n{transcript}",
            },
        ]
        try:
            result = await asyncio.wait_for(
                self._ctx.llm.generate(
                    prompt=prompt,
                    model=self._config.summary_task_name,
                    temperature=self._config.summary_temperature,
                    max_tokens=self._config.summary_max_tokens,
                ),
                timeout=max(0.1, self._config.write_timeout_seconds * 0.7),
            )
        except Exception as exc:
            self._logger.warning("生成通话记忆摘要失败，使用确定性摘要: %s", exc)
            return _fallback_summary(turns, self._config.max_summary_chars), ()
        if not isinstance(result, dict) or not result.get("success"):
            reason = result.get("error") if isinstance(result, dict) else result
            self._logger.warning("通话记忆摘要模型返回失败，使用确定性摘要: %s", reason)
            return _fallback_summary(turns, self._config.max_summary_chars), ()
        summary, facts = _parse_summary_payload(
            str(result.get("response") or ""),
            max_summary_chars=self._config.max_summary_chars,
            max_facts=self._config.max_facts,
            caller_texts=[turn.caller_text for turn in turns],
        )
        if not summary:
            summary = _fallback_summary(turns, self._config.max_summary_chars)
        return summary, facts

    async def _ensure_stream_id(self, archive: CallArchive) -> str:
        if archive.caller.stream_id:
            return archive.caller.stream_id
        session = await self._ctx.chat.open_session(
            platform="qq",
            chat_type="private",
            user_id=archive.caller.uin,
            account_id=archive.account_id,
            scope=archive.scope,
        )
        return _extract_stream_id(session)

    async def _append_context(self, stream_id: str, record: str, message_id: str) -> bool:
        if not self._config.append_maisaka_context:
            return False
        result = await self._ctx.maisaka.context.append(
            stream_id,
            [{"type": "text", "data": record}],
            visible_text=record,
            source_kind=CALL_ARCHIVE_SOURCE,
            message_id=message_id,
        )
        if isinstance(result, dict):
            return bool(result.get("success"))
        return bool(result)

    async def _persist_private_message(
        self,
        archive: CallArchive,
        record: str,
        message_id: str,
    ) -> bool:
        if not self._config.persist_private_session:
            return False
        message = {
            "message_id": message_id,
            "timestamp": str(archive.ended_at),
            "platform": "qq",
            "message_info": {
                "user_info": {
                    "user_id": archive.caller.uin,
                    "user_nickname": archive.caller.name or "用户",
                    "user_cardname": None,
                },
                "group_info": None,
                "additional_config": {
                    "qq_voice_call_memory": True,
                    "qq_voice_call_invite_at": archive.invite_at,
                },
            },
            "raw_message": [{"type": "text", "data": record}],
            "processed_plain_text": record,
            "is_mentioned": False,
            "is_at": False,
            "is_emoji": False,
            "is_picture": False,
            "is_command": False,
            "is_notify": False,
        }
        return await self._ctx.gateway.route_message(
            GATEWAY_NAME,
            message,
            route_metadata={
                "platform_io_account_id": archive.account_id,
                "platform_io_scope": archive.scope,
                "qq_voice_call_memory": True,
                "source_kind": CALL_ARCHIVE_SOURCE,
            },
            external_message_id=message_id,
            dedupe_key=message_id,
        )

    async def write(self, archive: CallArchive) -> MemoryWriteResult:
        if not self._config.enabled:
            return MemoryWriteResult(success=True, skipped_reason="通话记忆已禁用")
        turns = sanitize_call_turns(archive.turns, max_turns=self._config.max_turns)
        if len(turns) < self._config.min_turns:
            return MemoryWriteResult(
                success=True,
                turn_count=len(turns),
                skipped_reason="没有足够的有效通话轮次",
            )
        if not archive.caller.uin:
            return MemoryWriteResult(
                success=False,
                turn_count=len(turns),
                skipped_reason="无法确认来电者 QQ",
            )

        transcript = format_call_transcript(
            turns,
            max_chars=self._config.max_transcript_chars,
        )
        summary, facts = await self._summarize(archive.caller, turns, transcript)
        record = _format_archive_record(
            archive,
            transcript,
            summary,
            facts,
            include_transcript=self._config.include_transcript,
        )
        message_id = _build_message_id(archive)
        persisted = False
        context_appended = False
        if self._config.persist_private_session:
            try:
                persisted = await self._persist_private_message(archive, record, message_id)
            except Exception as exc:
                self._logger.warning("持久化通话记录到 MaiBot 私聊失败: %s", exc)
        if self._config.append_maisaka_context:
            try:
                stream_id = await self._ensure_stream_id(archive)
                if stream_id:
                    context_appended = await self._append_context(
                        stream_id,
                        record,
                        message_id,
                    )
                else:
                    self._logger.warning("无法打开来电者私聊会话，跳过当前上下文追加")
            except Exception as exc:
                self._logger.warning("追加通话记录到 Maisaka 当前上下文失败: %s", exc)

        success = (
            (not self._config.persist_private_session or persisted)
            and (not self._config.append_maisaka_context or context_appended)
        )
        return MemoryWriteResult(
            success=success,
            turn_count=len(turns),
            summary=summary,
            facts=facts,
            persisted=persisted,
            context_appended=context_appended,
        )
