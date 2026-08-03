import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from maibot_qq_voice_call.config import AudioSection, QQVoiceCallConfig, TTSSection
from maibot_qq_voice_call.orchestrator import CallOrchestrator
from maibot_qq_voice_call.providers.tts_dashscope import (
    DashScopeRealtimeTTS,
    TTSPlaybackHandle,
    TTSPlaybackInterrupted,
)


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


@pytest.mark.asyncio
async def test_playback_completion_waits_for_queued_audio() -> None:
    tts = object.__new__(DashScopeRealtimeTTS)
    tts.state_lock = asyncio.Lock()
    tts.config = TTSSection(playback_latency_ms=0)
    tts.generation = 3
    tts.playback_future = asyncio.get_running_loop().create_future()
    tts.first_audio_future = asyncio.get_running_loop().create_future()
    tts.response_started_at = 1.0
    tts.playback_until = time.monotonic() - 1.0

    await tts._complete_playback(3, tts.playback_future)

    assert tts.playback_future is None
    assert tts.first_audio_future is None
    assert tts.response_started_at == 0.0


@pytest.mark.asyncio
async def test_interrupt_marks_started_playback_incomplete() -> None:
    tts = object.__new__(DashScopeRealtimeTTS)
    tts.state_lock = asyncio.Lock()
    tts.generation = 1
    tts.receiver_task = None
    tts.playback_process = None
    tts.websocket = None
    tts.ready_future = None
    tts.first_audio_future = asyncio.get_running_loop().create_future()
    tts.first_audio_future.set_result(0.2)
    playback_future = asyncio.get_running_loop().create_future()
    tts.playback_future = playback_future
    tts.response_active = True
    tts.response_started_at = 1.0
    tts.playback_until = time.monotonic() + 1.0
    tts.status = SimpleNamespace(tts_connected=True)

    await tts._disconnect_locked(interrupted=True)

    with pytest.raises(TTSPlaybackInterrupted):
        await playback_future


@pytest.mark.asyncio
async def test_orchestrator_does_not_commit_interrupted_tts_as_spoken() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    completion = asyncio.get_running_loop().create_future()
    completion.set_exception(TTSPlaybackInterrupted())
    orchestrator.tts = SimpleNamespace(
        start=AsyncMock(
            return_value=TTSPlaybackHandle(
                first_audio_seconds=0.25,
                completion=completion,
            )
        )
    )

    assert await orchestrator.speak("这句话只播放了一部分") is False
    assert orchestrator.status.last_tts_seconds == 0.25


@pytest.mark.asyncio
async def test_failed_old_turn_does_not_disconnect_rewarmed_connection() -> None:
    tts = object.__new__(DashScopeRealtimeTTS)
    tts.turn_lock = asyncio.Lock()
    tts.state_lock = asyncio.Lock()
    tts.connection_lock = asyncio.Lock()
    tts.config = TTSSection(playback_latency_ms=0)
    tts.response_active = False
    tts.playback_future = None
    tts.playback_until = 0.0
    tts._ensure_connected = AsyncMock()
    tts._disconnect_locked = AsyncMock()

    replacement_first_audio = asyncio.get_running_loop().create_future()
    replacement_playback = asyncio.get_running_loop().create_future()

    async def fail_after_rewarm(_payload) -> None:
        tts.first_audio_future = replacement_first_audio
        tts.playback_future = replacement_playback
        raise RuntimeError("old connection failed")

    tts.websocket = SimpleNamespace(closed=False, send_json=fail_after_rewarm)

    with pytest.raises(RuntimeError, match="old connection failed"):
        await tts.start("测试")

    tts._disconnect_locked.assert_not_awaited()
    replacement_first_audio.cancel()
    replacement_playback.cancel()
