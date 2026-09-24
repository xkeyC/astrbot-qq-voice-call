"""QQ voice calls for AstrBot, through the NapCat AV bridge.

Calls run on Codex realtime by default, or on a local MiniCPM-o server
(``voice_backend = minicpm_omni``).

The bridge (``bridge/``) answers QQ calls and streams the call over one
WebSocket: text frames carry the call state, binary frames carry audio
(16-bit mono PCM at 48 kHz) both ways. Each call becomes an
``astrbot.core.voice`` session paired with the caller's private chat, so the
voice agent gets the tools and memories an ordinary member has there.

Needs the AstrBot Codex fork (``astrbot.core.voice``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path

import aiohttp

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star

CALL_PROMPT = """Your name is {name}. You are on a QQ voice call, one to one, with {caller}. Everything you hear is meant for you.

Talk like on the phone: brief, natural, in the caller's language. Delegate real tasks (anything needing facts, lookups or work) to the backend and tell the caller the result briefly."""

OUTGOING_PROMPT = """You placed this call yourself. The reason: {purpose}"""

# Told to the realtime model once the call is up, so that the bot speaks
# first, as whoever answers or places a phone call does.
ANSWER_CUE = "(The call is connected. Answer the phone with a short greeting.)"
DIAL_CUE = "(The call is connected. Greet them and briefly say why you are calling.)"
# The omni backend's opening is a purpose the voice agent words (OPENING_PROMPT).
OMNI_ANSWER_PURPOSE = "对方打来的电话刚接通：简短地打个招呼。"

RECONNECT_SECONDS = 5.0
CONNECT_TIMEOUT = 15.0
# How long a connected call waits for the bridge to name the caller.
IDENTITY_WAIT = 3.0
# How long an outgoing call may take to be answered before it is forgotten.
DIAL_TIMEOUT = 90.0
# The bridge may take a while to dial (uid lookup, AV host round trips).
DIAL_REQUEST_TIMEOUT = 30.0
IDLE_HANGUP_SECONDS = 120.0
# Attempts, and the pause between them, to end a call the bridge failed to.
HANGUP_ATTEMPTS = 3
HANGUP_RETRY_SECONDS = 10.0
# Upper bound of the bot's speech queued ahead with Codex realtime.
REALTIME_BUFFER = 3.0


class QQVoiceCallPlugin(Star):
    """接听与拨打 QQ 语音电话，由 Codex 实时语音全双工对话。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.call: dict = {}
        self.session = None  # the VoiceSession of the call in progress
        self.session_invite = ""
        # Outgoing call waiting to be answered: {"uin", "purpose", "at"}.
        self.dialing: dict | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._http: aiohttp.ClientSession | None = None
        self._out: asyncio.Queue[bytes] = asyncio.Queue(maxsize=250)
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    async def initialize(self) -> None:
        self._http = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._run(), name="qq-voice-call")

    async def terminate(self) -> None:
        for task in [self._task, *self._tasks]:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._end_call("plugin unloaded")
        if self._http is not None:
            await self._http.close()

    # -- bridge -------------------------------------------------------------

    def _base_url(self) -> str:
        return str(self.config.get("bridge_url") or "http://127.0.0.1:6110").rstrip("/")

    def _headers(self) -> dict:
        token = str(self.config.get("bridge_token") or "").strip()
        if not token and (path := str(self.config.get("bridge_token_file") or "")):
            token = Path(path).expanduser().read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("bridge_token or bridge_token_file is required")
        return {"Authorization": f"Bearer {token}"}

    async def _run(self) -> None:
        """Keeps the bridge stream open, reconnecting after failures."""
        assert self._http is not None
        url = self._base_url().replace("http", "ws", 1) + "/v1/stream"
        while True:
            try:
                # The handshake has its own timeout: a proxy or relay that
                # accepts the connection but never answers would hang here.
                ws = await asyncio.wait_for(
                    self._http.ws_connect(url, headers=self._headers(), heartbeat=15),
                    CONNECT_TIMEOUT,
                )
                async with ws:
                    self._ws = ws
                    logger.info("QQ voice call: bridge stream connected")
                    writer = asyncio.create_task(self._write(ws))
                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                if self.session is not None:
                                    self.session.media.feed(msg.data)
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get("type") == "call":
                                    await self._on_call(data.get("call") or {})
                    finally:
                        writer.cancel()
                        self._ws = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retried
                logger.warning("QQ voice call: bridge stream failed: %s", exc)
            await self._end_call("bridge stream lost")
            await asyncio.sleep(RECONNECT_SECONDS)

    async def _write(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await ws.send_bytes(await self._out.get())

    def _send_audio(self, chunk: bytes) -> None:
        if self._ws is None:
            return
        if self._out.full():  # the bridge fell behind: drop the oldest audio
            with contextlib.suppress(asyncio.QueueEmpty):
                self._out.get_nowait()
        self._out.put_nowait(chunk)

    # -- calls --------------------------------------------------------------

    async def _on_call(self, call: dict) -> None:
        self.call = call
        phase = call.get("phase")
        invite = str(call.get("inviteAt") or "")
        if phase == "connected" and invite and invite != self.session_invite:
            await self._end_call("replaced by a new call")
            self.session_invite = invite
            self._spawn(self._start_call(invite))
        elif phase != "connected" and self.session_invite:
            await self._end_call(f"call {phase}")

    async def _start_call(self, invite: str) -> None:
        from astrbot.core.voice import omni
        from astrbot.core.voice.pcm import PcmMedia
        from astrbot.core.voice.session import VoiceOptions, VoiceSession, time_prompt

        # The caller's QQ number is looked up by the bridge; give it a moment.
        deadline = time.monotonic() + IDENTITY_WAIT
        while not self.call.get("callerUin") and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if self.session_invite != invite:
            return
        uin = str(self.call.get("callerUin") or "")
        if not uin:
            # The bridge answered already: a silent line helps nobody.
            await self._hang_up_call(invite, "caller unknown")
            return
        platform_id = self._platform_id()
        if not platform_id:
            logger.error("QQ voice call: no aiocqhttp platform to pair calls with")
            await self._hang_up_call(invite, "no aiocqhttp platform")
            return
        dialing = self.dialing
        outgoing = bool(self.call.get("outgoing"))
        purpose = (
            dialing["purpose"]
            if outgoing
            and dialing is not None
            and dialing["uin"] == uin
            and time.monotonic() - dialing["at"] < DIAL_TIMEOUT
            else ""
        )
        self.dialing = None
        caller = str(self.call.get("callerName") or uin)
        options = VoiceOptions(
            name=str(self.config.get("voice_name") or "AstrBot"),
            aliases=[],
            voice=str(self.config.get("voice") or ""),
            model=str(self.config.get("voice_model") or ""),
            extra_prompt=str(self.config.get("voice_prompt") or ""),
            agent_instructions=str(self.config.get("agent_instructions") or ""),
            # UDP to the realtime peer loses packets on long paths, heard as
            # choppy audio; TCP does not (see astrbot.core.voice.icetcp).
            media_tcp=bool(self.config.get("media_over_tcp", True)),
        )
        local = self.config.get("voice_backend") == "minicpm_omni"
        backend: dict = {}
        if local:
            # A call is one to one: no silence bias, as for a Mumble whisper.
            backend = {
                "omni": omni.OmniOptions(
                    url=str(self.config.get("omni_url") or omni.OmniOptions.url),
                    ref_audio=str(self.config.get("omni_ref_audio") or "").strip(),
                    silence_bias=0.0,
                    # Empty on purpose means no acknowledgement.
                    tool_filler=str(
                        self.config.get(
                            "omni_tool_filler", omni.OmniOptions.tool_filler
                        )
                        or ""
                    ),
                    asr_dir=str(self.config.get("omni_asr_dir") or "").strip(),
                ),
                "group": False,
            }
            prompt = omni.duplex_prompt(options.name, options.extra_prompt, caller)
            opening = purpose or OMNI_ANSWER_PURPOSE
        else:
            prompt = CALL_PROMPT.format(name=options.name, caller=caller)
            if outgoing:
                prompt += "\n\n" + OUTGOING_PROMPT.format(
                    purpose=purpose or "not given"
                )
            prompt += "\n\n" + time_prompt()
            if options.extra_prompt:
                prompt += "\n\n" + options.extra_prompt
            opening = DIAL_CUE if outgoing else ANSWER_CUE

        def closed(session) -> None:
            if self.session is session:
                self.session = None
            # The session failed or ended on its own (realtime closed, WebRTC
            # failed, the omni server refused it) while the call is still up:
            # end the call rather than leave a silent line. A call ending first
            # clears session_invite before closing the session; the invite is
            # kept here so the same call does not get a new session.
            if self.session_invite == invite:
                self._spawn(self._hang_up_call(invite, "voice session ended"))

        session = (omni.OmniVoiceSession if local else VoiceSession)(
            **backend,
            key=f"call:{uin}",
            scope_id=f"{platform_id}:voice:call:{uin}",
            prompt=prompt,
            options=options,
            # Queued and paced out: WebRTC hands over a realtime model's
            # speech in bursts, and the omni server delivers it ahead of time
            # and cuts it on barge-in.
            media=PcmMedia(
                self._send_audio,
                buffer_seconds=omni.PLAYOUT_BUFFER_SECONDS
                if local
                else REALTIME_BUFFER,
                # A realtime peer sends silence all along: skipping it while a
                # backlog exists keeps a stall from adding lasting latency.
                trim_silence=not local,
            ),
            on_closed=closed,
            # The caller's private chat: its member tools and memories.
            memory_scope=f"{platform_id}:FriendMessage:{uin}",
            label="QQ call",
        )
        self.session = session
        session.launch(
            lambda exc: logger.error("QQ voice call with %s failed: %s", uin, exc)
        )
        logger.info(
            "QQ voice call %s %s (%s)", "to" if outgoing else "from", caller, uin
        )
        # The session has its own timeouts (a first omni session loads the
        # models); a failed start closes it, and closing hangs up.
        while not session.ready and not session.closing:
            await asyncio.sleep(0.1)
        if session.closing:
            return
        try:
            await session.say(opening)
        except RuntimeError as exc:  # closed in the meantime
            logger.info("QQ voice call with %s: no opening: %s", uin, exc)
        # Hang up a call nobody speaks in any more (a forgotten line).
        idle = float(self.config.get("idle_hangup_seconds") or IDLE_HANGUP_SECONDS)
        while self.session is session and not session.closing:
            if time.monotonic() - session.last_activity > idle:
                logger.info("QQ voice call with %s idle, hanging up", uin)
                await self._hang_up_call(invite, "idle")
                return
            await asyncio.sleep(1.0)

    async def _hang_up_call(self, invite: str, reason: str) -> None:
        """Ends the call ``invite`` at the bridge if it is still the call there.

        Args:
            invite: The call's ``inviteAt``.
            reason: Why, for the log.
        """
        if str(self.call.get("inviteAt") or "") != invite or self.call.get("phase") in (
            "idle",
            "ended",
            "error",
        ):
            return
        logger.info("QQ voice call %s: hanging up (%s)", invite, reason)
        for attempt in range(HANGUP_ATTEMPTS):
            try:
                await self._hangup()
                return
            except Exception as exc:  # noqa: BLE001 - logged and retried
                logger.warning("QQ voice call %s: hang-up failed: %s", invite, exc)
            if attempt + 1 < HANGUP_ATTEMPTS:
                await asyncio.sleep(HANGUP_RETRY_SECONDS)
            if str(self.call.get("inviteAt") or "") != invite:
                return

    async def _hangup(self) -> dict:
        """Asks the bridge to end the call in progress; returns its answer."""
        assert self._http is not None
        async with self._http.post(
            self._base_url() + "/v1/calls/hangup",
            headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 300:
                raise RuntimeError(body.get("message") or f"HTTP {resp.status}")
            return body.get("data") or {}

    async def _end_call(self, reason: str) -> None:
        session, self.session = self.session, None
        self.session_invite = ""
        while not self._out.empty():
            self._out.get_nowait()
        if session is not None:
            await session.close(reason)

    def _platform_id(self) -> str:
        if configured := str(self.config.get("platform_id") or ""):
            return configured
        for platform in self.context.platform_manager.platform_insts:
            if platform.meta().name == "aiocqhttp":
                return platform.meta().id
        return ""

    def _spawn(self, coro) -> None:
        def done(task: asyncio.Task) -> None:
            self._tasks.discard(task)
            if not task.cancelled() and (exc := task.exception()) is not None:
                logger.error("QQ voice call task failed: %s", exc, exc_info=exc)

        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(done)

    # -- tools --------------------------------------------------------------

    @llm_tool("qq_voice_call")
    async def qq_voice_call(
        self, event: AstrMessageEvent, purpose: str, user_id: str = ""
    ) -> str:
        """给 QQ 用户打语音电话。接通后由你（实时语音）在电话里和对方交谈，本工具只负责拨号，拨出后立即返回。

        Args:
            purpose(string): 打这通电话的原因和要说的事，接通后会据此开口，要写清楚。
            user_id(string): 可选。对方的 QQ 号。不填则打给当前对话的发起人。
        """
        uin = str(user_id or event.get_sender_id()).strip()
        if not uin.isdigit():
            return json.dumps({"error": "user_id 必须是 QQ 号"}, ensure_ascii=False)
        if self._ws is None or self._http is None:
            return json.dumps({"error": "QQ 通话桥未连接"}, ensure_ascii=False)
        if self.session is not None or self.call.get("phase") not in (
            None,
            "idle",
            "ended",
            "error",
        ):
            return json.dumps({"error": "正在通话中，稍后再拨"}, ensure_ascii=False)
        # Set before dialing: the call may connect before the answer arrives.
        self.dialing = {"uin": uin, "purpose": purpose, "at": time.monotonic()}
        try:
            async with self._http.post(
                self._base_url() + "/v1/calls/dial",
                json={"uin": uin},
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=DIAL_REQUEST_TIMEOUT),
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status >= 300:
                    self.dialing = None
                    return json.dumps(
                        {"error": f"拨号失败: {body.get('message') or resp.status}"},
                        ensure_ascii=False,
                    )
        except Exception as exc:  # noqa: BLE001 - reported to the model
            self.dialing = None
            return json.dumps({"error": f"拨号失败: {exc}"}, ensure_ascii=False)
        return json.dumps(
            {"status": "dialing", "user_id": uin, "note": "接通后会在电话里说明来意"},
            ensure_ascii=False,
        )

    @llm_tool("qq_voice_hangup")
    async def qq_voice_hangup(self, event: AstrMessageEvent) -> str:
        """挂断当前的 QQ 语音电话。通话中对方道别、说要挂了，或者事情已经说完时调用。"""
        if self._http is None:
            return json.dumps({"error": "QQ 通话桥未连接"}, ensure_ascii=False)
        try:
            data = await self._hangup()
        except Exception as exc:  # noqa: BLE001 - reported to the model
            return json.dumps({"error": f"挂断失败: {exc}"}, ensure_ascii=False)
        status = "hung up" if data.get("closed") else "no call in progress"
        return json.dumps({"status": status}, ensure_ascii=False)
