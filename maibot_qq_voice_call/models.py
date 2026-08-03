"""Serializable runtime models."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class RuntimeStatus:
    ready: bool = False
    call_phase: str = "idle"
    invite_at: str = ""
    active: bool = False
    recording: bool = False
    busy: bool = False
    asr_connected: bool = False
    tts_connected: bool = False
    caller_uid: str = ""
    caller_uin: str = ""
    caller_name: str = ""
    caller_person_id: str = ""
    caller_stream_id: str = ""
    caller_context_ready: bool = False
    utterance_count: int = 0
    ignored_utterance_count: int = 0
    dropped_utterance_count: int = 0
    queue_size: int = 0
    last_transcript: str = ""
    last_reply: str = ""
    last_asr_seconds: float = 0.0
    last_chat_seconds: float = 0.0
    last_tts_seconds: float = 0.0
    current_call_turn_count: int = 0
    last_memory_write_success: bool | None = None
    last_memory_write_seconds: float = 0.0
    last_memory_summary: str = ""
    last_memory_fact_count: int = 0
    last_memory_error: str = ""
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CallerContext:
    uid: str = ""
    uin: str = ""
    name: str = "用户"
    person_id: str = ""
    stream_id: str = ""
    prompt_context: str = ""
    memory_point_count: int = 0
    recent_message_count: int = 0


@dataclass(slots=True)
class CallUtterance:
    wav_bytes: bytes
    realtime_transcript: asyncio.Future[tuple[str, float]] | None = None
