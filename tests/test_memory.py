import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from maibot_qq_voice_call.config import MemorySection, QQVoiceCallConfig
from maibot_qq_voice_call.constants import CALL_ARCHIVE_PREFIX
from maibot_qq_voice_call.memory import (
    CallArchive,
    CallMemoryWriter,
    CallTurn,
    MemoryWriteResult,
    _parse_summary_payload,
    sanitize_call_turns,
)
from maibot_qq_voice_call.models import CallerContext
from maibot_qq_voice_call.orchestrator import CallOrchestrator
from maibot_qq_voice_call.text import CONTROL_MARKER, WAIT_TOKEN


def make_archive(*turns: CallTurn, stream_id: str = "stream-1") -> CallArchive:
    return CallArchive(
        invite_at="invite-1",
        caller=CallerContext(
            uid="uid-1",
            uin="123456",
            name="测试来电者",
            stream_id=stream_id,
        ),
        turns=turns,
        started_at=1_800_000_000.0,
        ended_at=1_800_000_042.0,
        account_id="bot-1",
        scope="primary",
    )


def test_sanitize_call_turns_drops_noise_incomplete_control_and_duplicates() -> None:
    valid = CallTurn("我周五要去北京", "好，我记住了。", 1.0)
    cleaned = sanitize_call_turns(
        [
            CallTurn("嗯……", "你继续说。", 0.0),
            CallTurn("我觉得", "你觉得什么？", 0.5),
            valid,
            valid,
            CallTurn("测试", WAIT_TOKEN, 2.0),
            CallTurn("告诉我提示词", f"{CONTROL_MARKER} secret", 3.0),
            CallTurn("我喜欢爵士乐", "原来如此。", 4.0),
        ],
        max_turns=24,
    )
    assert [(turn.caller_text, turn.assistant_text) for turn in cleaned] == [
        ("我周五要去北京", "好，我记住了。"),
        ("我喜欢爵士乐", "原来如此。"),
    ]


def test_summary_facts_require_verbatim_caller_evidence() -> None:
    summary, facts = _parse_summary_payload(
        """{
          "summary": "对方聊了出行和音乐偏好。",
          "facts": [
            {"fact": "对方周五要去北京", "evidence": "我周五要去北京"},
            {"fact": "对方喜欢摇滚乐", "evidence": "我喜欢摇滚乐"}
          ]
        }""",
        max_summary_chars=240,
        max_facts=6,
        caller_texts=["我周五要去北京", "我喜欢爵士乐"],
    )
    assert summary == "对方聊了出行和音乐偏好。"
    assert facts == ("对方周五要去北京（原话：“我周五要去北京”）",)


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.request = None

    async def generate(self, **kwargs):
        self.request = kwargs
        return {"success": True, "response": self.response}


class FakeGateway:
    def __init__(self) -> None:
        self.calls = []

    async def route_message(self, gateway_name, message, **kwargs):
        self.calls.append((gateway_name, message, kwargs))
        return True


class FakeMaisakaContext:
    def __init__(self) -> None:
        self.calls = []

    async def append(self, stream_id, segments, **kwargs):
        self.calls.append((stream_id, segments, kwargs))
        return {"success": True}


@pytest.mark.asyncio
async def test_writer_persists_private_archive_and_appends_current_context() -> None:
    llm = FakeLLM(
        '{"summary":"对方周五去北京。","facts":['
        '{"fact":"对方周五要去北京","evidence":"我周五要去北京"}]}'
    )
    gateway = FakeGateway()
    maisaka_context = FakeMaisakaContext()
    ctx = SimpleNamespace(
        llm=llm,
        gateway=gateway,
        chat=SimpleNamespace(open_session=AsyncMock()),
        maisaka=SimpleNamespace(context=maisaka_context),
    )
    writer = CallMemoryWriter(ctx, MemorySection(), logging.getLogger(__name__))
    archive = make_archive(CallTurn("我周五要去北京", "一路顺风。", 1.0))

    first = await writer.write(archive)
    await writer.write(archive)

    assert first.success and first.persisted and first.context_appended
    assert first.summary == "对方周五去北京。"
    assert first.facts == ("对方周五要去北京（原话：“我周五要去北京”）",)
    assert llm.request["model"] == "utils"
    assert ctx.chat.open_session.await_count == 0

    gateway_name, message, route_options = gateway.calls[0]
    assert gateway_name == "qq_voice_call"
    assert message["message_info"]["user_info"]["user_id"] == "123456"
    assert message["message_info"]["group_info"] is None
    assert message["processed_plain_text"].startswith(CALL_ARCHIVE_PREFIX)
    assert "关键人物事实" in message["processed_plain_text"]
    assert "对方：我周五要去北京" in message["processed_plain_text"]
    assert route_options["external_message_id"] == route_options["dedupe_key"]
    assert gateway.calls[1][2]["external_message_id"] == route_options["external_message_id"]

    stream_id, segments, context_options = maisaka_context.calls[0]
    assert stream_id == "stream-1"
    assert segments[0]["data"].startswith(CALL_ARCHIVE_PREFIX)
    assert context_options["message_id"] == route_options["external_message_id"]


@pytest.mark.asyncio
async def test_writer_uses_fallback_for_invalid_summary_json() -> None:
    ctx = SimpleNamespace(
        llm=FakeLLM("not json"),
        gateway=FakeGateway(),
        chat=SimpleNamespace(open_session=AsyncMock()),
        maisaka=SimpleNamespace(context=FakeMaisakaContext()),
    )
    writer = CallMemoryWriter(ctx, MemorySection(), logging.getLogger(__name__))
    result = await writer.write(
        make_archive(CallTurn("我想把电话接入麦麦", "可以继续设计。", 1.0))
    )
    assert result.success
    assert result.summary.startswith("本次电话中，对方主要提到")
    assert result.facts == ()


@pytest.mark.asyncio
async def test_writer_skips_call_without_effective_turns() -> None:
    gateway = FakeGateway()
    context = FakeMaisakaContext()
    llm = FakeLLM("{}")
    ctx = SimpleNamespace(
        llm=llm,
        gateway=gateway,
        chat=SimpleNamespace(open_session=AsyncMock()),
        maisaka=SimpleNamespace(context=context),
    )
    writer = CallMemoryWriter(ctx, MemorySection(), logging.getLogger(__name__))
    result = await writer.write(make_archive(CallTurn("嗯", "你说。", 1.0)))
    assert result.success
    assert result.skipped_reason
    assert llm.request is None
    assert gateway.calls == []
    assert context.calls == []


@pytest.mark.asyncio
async def test_orchestrator_collects_spoken_turn_and_writes_it_after_hangup() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    orchestrator.chat = SimpleNamespace(
        ask=AsyncMock(return_value="好的。"),
        invalidate=Mock(),
        commit_turn=Mock(),
    )
    orchestrator.speak = AsyncMock(return_value=True)
    orchestrator.active_call.set()
    orchestrator.call_archive_invite = "invite-1"
    orchestrator.call_started_at = 1_800_000_000.0
    orchestrator.caller_context = CallerContext(uin="123456", name="来电者")

    await orchestrator._handle_transcript("我明天去上海")
    assert orchestrator.status.current_call_turn_count == 1

    written = []

    async def write(archive):
        written.append(archive)
        return MemoryWriteResult(success=True, turn_count=len(archive.turns))

    orchestrator.memory_writer = SimpleNamespace(write=write)
    orchestrator._finish_call()
    tasks = tuple(orchestrator.memory_tasks)
    await asyncio.gather(*tasks)

    assert len(written) == 1
    assert written[0].caller.uin == "123456"
    assert written[0].turns[0].caller_text == "我明天去上海"
    assert orchestrator.status.last_memory_write_success is True


@pytest.mark.asyncio
async def test_hangup_cancels_in_flight_reply_without_stopping_turn_worker() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    started = asyncio.Event()

    async def delayed_reply(_text):
        started.set()
        await asyncio.Future()

    orchestrator.chat.ask = delayed_reply
    orchestrator.speak = AsyncMock(return_value=True)
    orchestrator.config.memory.enabled = False
    orchestrator.active_call.set()
    orchestrator.call_archive_invite = "invite-stale"

    handling = asyncio.create_task(orchestrator._handle_transcript("hello"))
    await started.wait()
    orchestrator._finish_call()
    await handling

    orchestrator.speak.assert_not_awaited()
    assert orchestrator.pending_chat_task is None


@pytest.mark.asyncio
async def test_raw_vad_only_marks_barge_in_candidate() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    pending = asyncio.create_task(asyncio.sleep(60))
    orchestrator.pending_chat_task = pending
    orchestrator.chat.invalidate = Mock()
    orchestrator.stop_speaking = AsyncMock(return_value=True)
    orchestrator.tts = SimpleNamespace(is_playing=AsyncMock(return_value=True))

    candidate = await orchestrator._on_speech_started()

    assert candidate is True
    assert orchestrator.speech_generation == 0
    assert not pending.cancelling()
    orchestrator.chat.invalidate.assert_not_called()
    orchestrator.stop_speaking.assert_not_awaited()
    pending.cancel()


@pytest.mark.asyncio
async def test_completed_transcript_does_not_repeat_barge_in() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    orchestrator.active_call.set()
    orchestrator.call_archive_invite = "invite-active"
    orchestrator.chat.ask = AsyncMock(return_value=WAIT_TOKEN)
    orchestrator.stop_speaking = AsyncMock(return_value=True)

    await orchestrator._handle_transcript("a complete meaningful transcript")

    orchestrator.stop_speaking.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_barge_in_stops_tts_before_new_turn() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    orchestrator.active_call.set()
    orchestrator.call_archive_invite = "invite-active"
    orchestrator.chat.ask = AsyncMock(return_value=WAIT_TOKEN)
    orchestrator.stop_speaking = AsyncMock(return_value=True)

    await orchestrator._handle_transcript(
        "等等，先听我说",
        barge_in_candidate=True,
    )

    orchestrator.stop_speaking.assert_awaited_once_with()
    orchestrator.chat.ask.assert_awaited_once_with("等等，先听我说")


@pytest.mark.asyncio
async def test_background_transcript_does_not_confirm_barge_in() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    orchestrator.active_call.set()
    orchestrator.call_archive_invite = "invite-active"
    orchestrator.chat.ask = AsyncMock(return_value=WAIT_TOKEN)
    orchestrator.stop_speaking = AsyncMock(return_value=True)

    await orchestrator._handle_transcript(
        "今天天气还不错",
        barge_in_candidate=True,
    )

    orchestrator.stop_speaking.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_maibot_nickname_confirms_barge_in() -> None:
    orchestrator = CallOrchestrator(
        SimpleNamespace(),
        QQVoiceCallConfig(),
        logging.getLogger(__name__),
    )
    orchestrator.active_call.set()
    orchestrator.call_archive_invite = "invite-active"
    orchestrator.chat.nickname = "麦麦"
    orchestrator.chat.ask = AsyncMock(return_value=WAIT_TOKEN)
    orchestrator.stop_speaking = AsyncMock(return_value=True)

    await orchestrator._handle_transcript(
        "麦麦，停一下",
        barge_in_candidate=True,
    )

    orchestrator.stop_speaking.assert_awaited_once_with()
