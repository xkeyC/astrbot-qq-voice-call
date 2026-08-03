"""Transcript gating and speech-safe text cleanup."""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable

WAIT_TOKEN = "[WAIT]"
CONTROL_MARKER = "[MAIBOT_QQ_CALL_CONTROL]"

_FILLER_PATTERN = re.compile(
    r"^(?:(?:嗯+|呃+|额+|啊+|哦+|唔+|哎+|喂+|哈+|"
    r"对+|对吧|是+|是吧|好+|好的|行+|可以+|没事|没有|"
    r"这个|那个|知道了?|明白了?|不好意思|"
    r"嗯哼+|咳+|咳咳+|h+m+|u+h+m+|u+h+|u+m+|oh+|ok(?:ay)?|yeah+|yep+))+$",
    re.IGNORECASE,
)
_INCOMPLETE_SUFFIXES = (
    "那个",
    "然后",
    "就是",
    "因为",
    "所以",
    "但是",
    "不过",
    "而且",
    "还有",
    "我想",
    "我觉得",
    "其实",
    "比如",
    "就是说",
    "怎么说",
    "所以现在",
)
_INCOMPLETE_EXACT = frozenset({"so", "and", "but", "because", "the"})
_BARGE_IN_PREFIXES = (
    "等等",
    "等一下",
    "等一等",
    "停一下",
    "停一停",
    "先停一下",
    "打住",
    "别说了",
    "不要说了",
    "先别说",
    "你先别说",
    "听我说",
    "先听我说",
    "让我说",
    "我说的是",
    "不是这个",
    "不对",
)
_ENGLISH_BARGE_IN_PATTERN = re.compile(
    r"^\s*(?:please\s+)?(?:stop|wait|hold\s+on)\b",
    re.IGNORECASE,
)


def normalize_turn_text(text: str) -> str:
    return re.sub(r"[\s，。！？!?、；;：:…~～,.\"'“”‘’]+", "", text).strip()


def is_filler_transcript(text: str) -> bool:
    normalized = normalize_turn_text(text)
    return not normalized or bool(_FILLER_PATTERN.fullmatch(normalized))


def is_incomplete_transcript(text: str) -> bool:
    normalized = normalize_turn_text(text)
    return bool(
        normalized
        and (
            normalized.casefold() in _INCOMPLETE_EXACT
            or (len(normalized) <= 20 and normalized.endswith(_INCOMPLETE_SUFFIXES))
        )
    )


def is_barge_in_transcript(
    text: str,
    *,
    wake_names: tuple[str, ...] = (),
) -> bool:
    """Recognize an intentional interruption after VAD marks a TTS overlap."""

    normalized = normalize_turn_text(text)
    if not normalized:
        return False
    folded = normalized.casefold()
    for wake_name in wake_names:
        normalized_name = normalize_turn_text(wake_name).casefold()
        if normalized_name and folded.startswith(normalized_name):
            return True
    return folded.startswith(_BARGE_IN_PREFIXES) or bool(
        _ENGLISH_BARGE_IN_PATTERN.match(text)
    )


class TranscriptGate:
    """Hold obviously incomplete turns and drop non-semantic ASR fragments."""

    def __init__(
        self,
        pending_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pending_seconds = pending_seconds
        self.clock = clock
        self.pending_text = ""
        self.pending_at = 0.0

    def clear(self) -> None:
        self.pending_text = ""
        self.pending_at = 0.0

    def process(self, transcript: str) -> str | None:
        transcript = transcript.strip()
        now = self.clock()
        if is_filler_transcript(transcript):
            return None

        if self.pending_text and now - self.pending_at <= self.pending_seconds:
            transcript = f"{self.pending_text}，{transcript}"
        self.clear()

        if is_incomplete_transcript(transcript):
            self.pending_text = transcript
            self.pending_at = now
            return None
        return transcript


def clean_tts_text(text: str, *, max_chars: int = 24) -> str:
    """Remove non-speech markup and reject leaked internal control content."""

    if CONTROL_MARKER in text:
        return ""
    cleaned = text.replace(WAIT_TOKEN, "")
    cleaned = re.sub(r"!\[[^\]]*]\([^)]+\)", "", cleaned)
    cleaned = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[\[【](?:发送|图片|表情|表情包|动作|文件)[^\]】]*[\]】]", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"[`*_>#|~]", "", cleaned)
    cleaned = "".join(
        character
        for character in cleaned
        if not unicodedata.category(character).startswith("So")
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    shortened = cleaned[:max_chars]
    punctuation_index = max((shortened.rfind(mark) for mark in "。！？!?；;"), default=-1)
    if punctuation_index >= max(8, max_chars // 2):
        shortened = shortened[: punctuation_index + 1]
    return shortened.rstrip()
