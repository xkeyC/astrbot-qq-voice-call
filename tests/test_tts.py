from types import SimpleNamespace

import pytest

from maibot_qq_voice_call.config import AudioSection, TTSSection
from maibot_qq_voice_call.providers.tts_dashscope import DashScopeRealtimeTTS


@pytest.mark.asyncio
async def test_playback_process_uses_configured_jitter_buffer(monkeypatch) -> None:
    captured: list[str] = []
    sentinel = SimpleNamespace()

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.extend(args)
        return sentinel

    monkeypatch.setattr(
        "maibot_qq_voice_call.providers.tts_dashscope.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    tts = object.__new__(DashScopeRealtimeTTS)
    tts.config = TTSSection(playback_latency_ms=80)
    tts.audio = AudioSection()

    result = await tts._spawn_playback_process()

    assert result is sentinel
    assert "--latency-msec=80" in captured


@pytest.mark.asyncio
async def test_playback_process_keeps_safe_latency_floor(monkeypatch) -> None:
    captured: list[str] = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.extend(args)
        return SimpleNamespace()

    monkeypatch.setattr(
        "maibot_qq_voice_call.providers.tts_dashscope.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    tts = object.__new__(DashScopeRealtimeTTS)
    tts.config = TTSSection(playback_latency_ms=0)
    tts.audio = AudioSection()

    await tts._spawn_playback_process()

    assert "--latency-msec=20" in captured
