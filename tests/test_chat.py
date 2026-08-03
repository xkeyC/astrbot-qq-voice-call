import asyncio
from types import SimpleNamespace

import pytest

from maibot_qq_voice_call.chat import MaiBotPhoneChat
from maibot_qq_voice_call.config import ChatSection
from maibot_qq_voice_call.models import CallerContext
from maibot_qq_voice_call.text import CONTROL_MARKER, WAIT_TOKEN


class FakeLLM:
    def __init__(self) -> None:
        self.request = None

    async def generate(self, **kwargs):
        self.request = kwargs
        return {"success": True, "response": "可以，我们继续。"}


def test_default_chat_task_routes_to_deepseek_flash_task() -> None:
    assert ChatSection().task_name == "utils"


@pytest.mark.asyncio
async def test_chat_routes_through_maibot_and_keeps_caller_context() -> None:
    llm = FakeLLM()
    chat = MaiBotPhoneChat(
        SimpleNamespace(llm=llm),
        ChatSection(task_name="replyer"),
    )
    chat.reset(
        CallerContext(
            uin="123456",
            name="测试来电者",
            prompt_context="来电者喜欢低延迟语音。",
        )
    )
    reply = await chat.ask("现在延迟怎么样")
    assert reply == "可以，我们继续。"
    assert llm.request["model"] == "replyer"
    system_prompt = llm.request["prompt"][0]["content"]
    assert CONTROL_MARKER in system_prompt
    assert "来电者喜欢低延迟语音" in system_prompt
    assert "不要自称 MaiBot" in system_prompt


@pytest.mark.asyncio
async def test_chat_commits_only_spoken_turns_to_history() -> None:
    llm = FakeLLM()
    chat = MaiBotPhoneChat(SimpleNamespace(llm=llm), ChatSection())

    first_reply = await chat.ask("第一句")
    await chat.ask("第二句")
    assert len(llm.request["prompt"]) == 2

    chat.commit_turn("第一句", first_reply)
    await chat.ask("第三句")
    assert llm.request["prompt"][-3:-1] == [
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": first_reply},
    ]


@pytest.mark.asyncio
async def test_chat_discards_reply_after_call_context_is_invalidated() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class DelayedLLM:
        async def generate(self, **kwargs):
            started.set()
            await release.wait()
            return {"success": True, "response": "stale reply"}

    chat = MaiBotPhoneChat(SimpleNamespace(llm=DelayedLLM()), ChatSection())
    chat.reset(CallerContext(uin="first"))
    task = asyncio.create_task(chat.ask("hello"))
    await started.wait()
    chat.invalidate()
    release.set()

    assert await task == WAIT_TOKEN
