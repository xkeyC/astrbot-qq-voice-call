import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from maibot_qq_voice_call.config import QQVoiceCallConfig
from maibot_qq_voice_call.models import CallerContext, CallUtterance
from maibot_qq_voice_call.orchestrator import CallOrchestrator


def ready_orchestrator() -> CallOrchestrator:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    orchestrator.active_call.set()
    orchestrator.status.invite_at = "invite-1"
    orchestrator.call_archive_invite = "invite-1"
    orchestrator.caller_context = CallerContext(
        uin="123456",
        recent_message_count=1,
    )
    orchestrator.speak = AsyncMock(return_value=True)
    orchestrator.chat.generate_greeting = AsyncMock(return_value="明天去上海，东西都收拾好了吗？")
    orchestrator.chat.commit_greeting = Mock()
    return orchestrator


@pytest.mark.asyncio
async def test_greet_plays_and_commits_contextual_opening() -> None:
    orchestrator = ready_orchestrator()

    await orchestrator.greet("invite-1")

    orchestrator.chat.generate_greeting.assert_awaited_once_with()
    orchestrator.speak.assert_awaited_once_with("明天去上海，东西都收拾好了吗？")
    orchestrator.chat.commit_greeting.assert_called_once_with(
        "明天去上海，东西都收拾好了吗？"
    )
    assert orchestrator.status.last_greeting_contextual is True


@pytest.mark.asyncio
async def test_greet_falls_back_when_context_generation_fails() -> None:
    orchestrator = ready_orchestrator()
    orchestrator.chat.generate_greeting = AsyncMock(side_effect=RuntimeError("provider failed"))

    await orchestrator.greet("invite-1")

    orchestrator.speak.assert_awaited_once_with(orchestrator.config.chat.greeting)
    orchestrator.chat.commit_greeting.assert_not_called()
    assert orchestrator.status.last_greeting_contextual is False


@pytest.mark.asyncio
async def test_greet_uses_static_opening_without_prior_context() -> None:
    orchestrator = ready_orchestrator()
    orchestrator.caller_context = CallerContext(uin="123456")

    await orchestrator.greet("invite-1")

    orchestrator.chat.generate_greeting.assert_not_awaited()
    orchestrator.speak.assert_awaited_once_with(orchestrator.config.chat.greeting)


@pytest.mark.asyncio
async def test_greet_skips_playback_when_caller_audio_is_already_queued() -> None:
    orchestrator = ready_orchestrator()
    orchestrator.utterance_queue.put_nowait(CallUtterance(wav_bytes=b"wav"))

    await orchestrator.greet("invite-1")

    orchestrator.speak.assert_not_awaited()
    orchestrator.chat.commit_greeting.assert_not_called()


@pytest.mark.asyncio
async def test_sustained_caller_speech_cancels_pending_greeting_generation() -> None:
    orchestrator = ready_orchestrator()
    started = asyncio.Event()

    async def delayed_greeting() -> str:
        started.set()
        await asyncio.Future()

    orchestrator.chat.generate_greeting = AsyncMock(side_effect=delayed_greeting)
    orchestrator.stop_speaking = AsyncMock(return_value=False)
    greeting = asyncio.create_task(orchestrator.greet("invite-1"))
    await started.wait()

    await orchestrator._on_speech_started()
    await greeting

    orchestrator.speak.assert_not_awaited()
    orchestrator.stop_speaking.assert_awaited_once_with()
