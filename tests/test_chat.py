from types import SimpleNamespace

import pytest

from maibot_qq_voice_call.chat import MaiBotPhoneChat
from maibot_qq_voice_call.config import ChatSection
from maibot_qq_voice_call.models import CallerContext
from maibot_qq_voice_call.text import CONTROL_MARKER


class FakeLLM:
    def __init__(self) -> None:
        self.request = None

    async def generate(self, **kwargs):
        self.request = kwargs
        return {"success": True, "response": "可以，我们继续。"}


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
