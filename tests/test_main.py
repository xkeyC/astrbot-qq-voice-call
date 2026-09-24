"""Plugin tests against a fake bridge.

Run with the Python environment of an AstrBot Codex fork checkout, e.g.
``PYTHONPATH=/path/to/AstrBot python -m pytest tests``.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 40


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "astrbot_plugin_qq_voice_call_main", ROOT / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin_main = load_plugin()


class FakeBridge:
    def __init__(self, dial_status: int = 200) -> None:
        self.dial_status = dial_status
        self.dialed: list[dict] = []
        self.hangups = 0
        self.audio: list[bytes] = []
        self.ws: web.WebSocketResponse | None = None
        self.connected = asyncio.Event()

    async def stream(self, request: web.Request) -> web.WebSocketResponse:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws = ws
        await ws.send_str(json.dumps({"type": "call", "call": {"phase": "idle"}}))
        self.connected.set()
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                self.audio.append(msg.data)
        return ws

    async def dial(self, request: web.Request) -> web.Response:
        self.dialed.append(await request.json())
        if self.dial_status != 200:
            return web.json_response(
                {"code": -1, "message": "a call is in progress"},
                status=self.dial_status,
            )
        return web.json_response({"code": 0})

    async def hangup(self, request: web.Request) -> web.Response:
        self.hangups += 1
        return web.json_response({"code": 0, "data": {"closed": True}})

    async def call(self, **call) -> None:
        await self.ws.send_str(json.dumps({"type": "call", "call": call}))


class FakeSession:
    instances: list["FakeSession"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.media = kwargs["media"]
        self.ready = False
        self.closing = False
        self.said: list[str] = []
        self.closed_reason = None
        self.last_activity = 1e18  # never idle unless a test says so
        FakeSession.instances.append(self)

    def launch(self, on_failed) -> None:
        self.ready = True

    async def say(self, text: str) -> None:
        self.said.append(text)

    async def close(self, reason: str = "") -> None:
        self.closing = True
        self.closed_reason = reason
        self.kwargs["on_closed"](self)


class FakeOmniSession(FakeSession):
    def __init__(self, *, omni, group, **kwargs) -> None:
        super().__init__(**kwargs)
        self.omni = omni
        self.group = group


class FakePlatform:
    def meta(self):
        class Meta:
            name = "aiocqhttp"
            id = "qq1"

        return Meta()


class FakeContext:
    class platform_manager:
        platform_insts = [FakePlatform()]


class FakeEvent:
    def get_sender_id(self) -> str:
        return "42"


async def wait_for(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


@pytest.fixture
async def setup(monkeypatch):
    from astrbot.core.voice import session as voice_session

    from astrbot.core.voice import omni

    FakeSession.instances.clear()
    monkeypatch.setattr(voice_session, "VoiceSession", FakeSession)
    monkeypatch.setattr(omni, "OmniVoiceSession", FakeOmniSession)
    bridge = FakeBridge()
    app = web.Application()
    app.router.add_get("/v1/stream", bridge.stream)
    app.router.add_post("/v1/calls/dial", bridge.dial)
    app.router.add_post("/v1/calls/hangup", bridge.hangup)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    plugin = plugin_main.QQVoiceCallPlugin(
        FakeContext(),
        {"bridge_url": f"http://127.0.0.1:{port}", "bridge_token": TOKEN},
    )
    await plugin.initialize()
    await asyncio.wait_for(bridge.connected.wait(), 5)
    await wait_for(lambda: plugin._ws is not None)
    yield plugin, bridge
    await plugin.terminate()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_incoming_call_becomes_a_voice_session(setup):
    plugin, bridge = setup
    await bridge.call(phase="ringing", inviteAt="a")
    await bridge.call(
        phase="connected", inviteAt="a", callerUin="123", callerName="Alice"
    )
    await wait_for(lambda: FakeSession.instances and FakeSession.instances[0].said)
    session = FakeSession.instances[0]
    assert session.kwargs["memory_scope"] == "qq1:FriendMessage:123"
    assert session.kwargs["scope_id"] == "qq1:voice:call:123"
    assert "with Alice" in session.kwargs["prompt"]
    assert "placed this call" not in session.kwargs["prompt"]
    assert session.said == [plugin_main.ANSWER_CUE]

    # Caller audio reaches the model, the model's voice reaches the bridge.
    await bridge.ws.send_bytes(b"\x01\x00" * 960)
    await wait_for(lambda: len(session.media._buffer) == 1920)
    session.media._send(b"\x02\x00" * 960)
    await wait_for(lambda: bridge.audio)
    assert bridge.audio[0] == b"\x02\x00" * 960

    await bridge.call(phase="ended", inviteAt="a", callerUin="123")
    await wait_for(lambda: session.closed_reason is not None)
    assert session.closed_reason == "call ended"
    assert plugin.session is None


@pytest.mark.asyncio
async def test_unknown_caller_is_not_answered(setup, monkeypatch):
    monkeypatch.setattr(plugin_main, "IDENTITY_WAIT", 0.1)
    plugin, bridge = setup
    await bridge.call(phase="connected", inviteAt="b")
    await asyncio.sleep(0.3)
    assert FakeSession.instances == []


@pytest.mark.asyncio
async def test_dialing_then_answer_speaks_the_purpose(setup):
    plugin, bridge = setup
    result = json.loads(await plugin.qq_voice_call(FakeEvent(), purpose="提醒明天开会"))
    assert result["status"] == "dialing"
    assert bridge.dialed == [{"uin": "42"}]
    await bridge.call(phase="dialing", inviteAt="c", callerUin="42", outgoing=True)
    await bridge.call(phase="connected", inviteAt="c", callerUin="42", outgoing=True)
    await wait_for(lambda: FakeSession.instances and FakeSession.instances[0].said)
    session = FakeSession.instances[0]
    assert "提醒明天开会" in session.kwargs["prompt"]
    assert session.said == [plugin_main.DIAL_CUE]
    busy = json.loads(await plugin.qq_voice_call(FakeEvent(), purpose="x"))
    assert "error" in busy


@pytest.mark.asyncio
async def test_dial_refused_by_the_bridge_is_reported(setup):
    plugin, bridge = setup
    bridge.dial_status = 409
    result = json.loads(
        await plugin.qq_voice_call(FakeEvent(), purpose="x", user_id="7")
    )
    assert "a call is in progress" in result["error"]
    assert plugin.dialing is None
    bad = json.loads(
        await plugin.qq_voice_call(FakeEvent(), purpose="x", user_id="abc")
    )
    assert "error" in bad


@pytest.mark.asyncio
async def test_hangup_tool_and_idle_hangup(setup):
    plugin, bridge = setup
    result = json.loads(await plugin.qq_voice_hangup(FakeEvent()))
    assert result == {"status": "hung up"}
    assert bridge.hangups == 1
    plugin.config["idle_hangup_seconds"] = 0.2
    await bridge.call(phase="connected", inviteAt="d", callerUin="5")
    await wait_for(lambda: FakeSession.instances and FakeSession.instances[0].said)
    FakeSession.instances[0].last_activity = 0.0
    await wait_for(lambda: bridge.hangups == 2)


@pytest.mark.asyncio
async def test_codex_is_the_default_backend(setup):
    plugin, bridge = setup
    await bridge.call(phase="connected", inviteAt="e", callerUin="8")
    await wait_for(lambda: FakeSession.instances and FakeSession.instances[0].said)
    session = FakeSession.instances[0]
    assert type(session) is FakeSession
    assert session.media._queue is None  # realtime audio passes straight on


@pytest.mark.asyncio
async def test_omni_backend_answers_and_dials_with_a_purpose(setup):
    plugin, bridge = setup
    plugin.config.update(
        voice_backend="minicpm_omni", omni_url="ws://omni:1/backend", voice_name="小Q"
    )
    await bridge.call(phase="connected", inviteAt="f", callerUin="9", callerName="Bob")
    await wait_for(lambda: FakeSession.instances and FakeSession.instances[0].said)
    session = FakeSession.instances[0]
    assert isinstance(session, FakeOmniSession)
    assert session.omni.url == "ws://omni:1/backend"
    assert session.omni.silence_bias == 0.0
    assert session.group is False
    assert "Bob" in session.kwargs["prompt"] and "小Q" in session.kwargs["prompt"]
    assert session.media._queue is not None  # paced, so barge-in can flush it
    assert session.said == [plugin_main.OMNI_ANSWER_PURPOSE]

    await bridge.call(phase="ended", inviteAt="f", callerUin="9")
    await wait_for(lambda: plugin.session is None)
    await plugin.qq_voice_call(FakeEvent(), purpose="提醒交房租")
    await bridge.call(phase="connected", inviteAt="g", callerUin="42", outgoing=True)
    await wait_for(
        lambda: len(FakeSession.instances) == 2 and FakeSession.instances[1].said
    )
    assert FakeSession.instances[1].said == ["提醒交房租"]
