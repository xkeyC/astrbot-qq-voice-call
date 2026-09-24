# Architecture

```text
QQ / NapCat (Linux)
    │ call signalling + native audio (AVSDK, second QQ process = AV Host)
    ▼
NapCat bridge plugin ── WebSocket /v1/stream ──────────────┐
    │  text frames: call state                              │
    │  binary frames: PCM 48 kHz mono, both ways            ▼
    ├─ parec ← astrbot_qq_speaker.monitor          AstrBot plugin (main.py)
    └─ pacat → astrbot_qq_mic                              │ PcmMedia
                                                           ▼
                                        astrbot.core.voice.VoiceSession
                                           │ WebRTC (aiortc)      │ voice agent thread
                                           ▼                      ▼
                                   Codex realtime (v3)    tools / memories of the
                                   listens and speaks     caller's private chat
```

## Components

### Bridge (`bridge/`)

- Answers incoming calls automatically, tracks the call phase and the caller's
  QQ identity, and owns the PulseAudio devices QQ uses as speaker and
  microphone.
- Streams the call over one authenticated WebSocket, so AstrBot needs neither
  PulseAudio nor to share a host with QQ.
- Hides every QQ/NapCat/AVSDK version detail.

### AstrBot plugin (`main.py`)

- Keeps the bridge stream open and reconnects after failures.
- On `connected`, starts a `VoiceSession` keyed by the caller, paired with
  `<aiocqhttp platform>:FriendMessage:<uin>`; closes it when the phase leaves
  `connected`.
- Tells the realtime model the call is up, so the bot speaks first.
- Offers the `qq_voice_call` tool; its permission is whatever the tool
  permission rules give it.

### Core voice session (AstrBot fork, `astrbot/core/voice/`)

Shared with the Mumble platform: WebRTC signalling through the Codex binding,
the voice agent thread and its member tools, proxy relay over ICE-TCP, and the
session lifecycle. The plugin only supplies audio (`PcmMedia`) and prompts.

## Call lifecycle

1. The bridge reports `ringing`, accepts, and reports `connected` once AVSDK
   entered the room.
2. The plugin waits briefly for the caller's QQ number, opens the voice
   session, and holds the caller's audio until the model listens.
3. The model hears the caller continuously; barge-in is handled by the
   realtime server. Real tasks are handed to the voice agent thread.
4. When the phase leaves `connected`, the session closes; the voice thread is
   unloaded and resumed on the next call from the same person.

## Outgoing calls

`qq_voice_call` remembers the purpose, posts `/v1/calls/dial` and returns at
once. The bridge resolves the uid and sends AVSDK command 4 (`StartCall`);
signalling then flows through the same kernel relay as incoming calls. When
the call connects (the bridge marks it `outgoing`), the purpose goes into the
realtime prompt and the model opens with it. `qq_voice_hangup` and the idle
timeout end a call with AVSDK command 10 (`Close`).
