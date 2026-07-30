from types import SimpleNamespace

import pytest

from maibot_qq_voice_call.config import ChatSection
from maibot_qq_voice_call.context import CallerContextResolver


class FakePerson:
    async def get_id(self, platform: str, user_id: str):
        assert platform == "qq"
        assert user_id == "123456"
        return {"person_id": "person-1"}

    async def get_value(self, person_id: str, field_name: str):
        values = {
            "person_name": "测试来电者",
            "memory_points": ["喜欢低延迟语音", "正在测试 QQ 电话"],
        }
        return values.get(field_name)


class FakeChat:
    async def open_session(self, **kwargs):
        return {"stream_id": "stream-1", "created": False}


class FakeMessage:
    async def get_recent(self, chat_id: str, limit: int):
        assert chat_id == "stream-1"
        return [{"text": "上一条消息"}]

    async def build_readable(self, messages, **kwargs):
        return "测试来电者：上一条消息"


@pytest.mark.asyncio
async def test_context_uses_public_sdk_capabilities() -> None:
    ctx = SimpleNamespace(
        person=FakePerson(),
        chat=FakeChat(),
        message=FakeMessage(),
    )
    resolver = CallerContextResolver(ctx, ChatSection())
    result = await resolver.resolve(
        {
            "callerUid": "uid-1",
            "callerUin": "123456",
            "callerName": "桥接昵称",
        },
        account_id="bot-1",
        scope="primary",
    )
    assert result.person_id == "person-1"
    assert result.stream_id == "stream-1"
    assert result.name == "测试来电者"
    assert "喜欢低延迟语音" in result.prompt_context
    assert "上一条消息" in result.prompt_context
