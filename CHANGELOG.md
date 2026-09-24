# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `voice_backend` option: `codex_realtime` (default) or `minicpm_omni`, the
  local MiniCPM-o server of the AstrBot fork's omni voice backend, with
  `omni_url`, `omni_ref_audio`, `omni_tool_filler` and `omni_asr_dir`. Omni
  speech is paced out through PcmMedia's playout buffer so barge-in can cut it.

## [0.4.0] - 2026-09-24

### Changed

- Ported from MaiBot to AstrBot (Codex fork): calls run on AstrBot's shared
  Codex realtime voice session (full duplex, barge-in, a voice agent thread
  with the caller's private-chat tools and memories) instead of chained
  DashScope ASR, LLM and TTS.
- Renamed every `MAIBOT_QQ_CALL_*` variable, device, path and NapCat plugin
  name to `ASTRBOT_QQ_CALL_*` / `astrbot_qq_*` / `astrbot-qq-*`.
- The bridge control port may listen beyond loopback for AstrBot in another
  container; the AV host stays on loopback.

### Added

- `/v1/stream` WebSocket on the bridge: call state as text frames, call audio
  as 48 kHz PCM both ways.
- Outgoing calls: `/v1/calls/dial` (AVSDK `StartCall`, command 4) and
  `/v1/calls/hangup` (`Close`, command 10) on the bridge, from static analysis
  of `libAVSDKPlugin.so`; `qq_voice_call` and `qq_voice_hangup` LLM tools; idle
  calls hang up after `idle_hangup_seconds`.
- `docker/Dockerfile`: NapCat, Linux QQ and the bridge in one image.

### Fixed

- The bridge no longer logs in to the AV host again on every AVSDK log line
  (output 20050), which looped forever; the lines are kept in
  `/v1/status` instead. Output 20000 (channel registration) is now forwarded
  to the kernel like 20001.

### Removed

- DashScope ASR/TTS providers, local VAD, MaiBot memory write-back and the
  MaiBot SDK plugin.

## [0.3.4] - 2026-08-03

### Added

- Generate the call opening from verified recent QQ messages and MaiBot person
  memory when available, with a short timeout and the configured static greeting
  as a reliable fallback.
- Cancel or skip contextual greeting generation when the caller speaks first, and
  retain only a fully played contextual opening in the in-call model history.

## [0.3.3] - 2026-08-03

### Changed

- Remove live hard-coded filler and incomplete-sentence gating; keep cleanup only
  for post-call memory records and let the phone model use `[WAIT]` when needed.
- Remove dead reply-generation state and duplicate TTS text cleanup.
- Align phone reply limits at 128 tokens and 80 spoken characters.
- Track full audible TTS completion before committing a reply to call history,
  and expose dropped queued utterances instead of silently replacing them.

## [0.3.2] - 2026-08-03

### Added

- Load MaiBot's current nickname, personality and reply style for phone identity.
- Add a configurable PulseAudio playback jitter buffer and regression tests.

### Changed

- Raise reply token and text limits for reasoning-capable fast models.
- Tighten filler, incomplete-sentence and prompt-leak filtering.

### Fixed

- Cancel and discard stale model replies when the caller starts a new speech turn.
- Keep unplayed replies out of phone history and post-call memory.
- Parse serialized recent-message dictionaries returned by MaiBot 1.0.x correctly.
- Invalidate in-flight replies when calls end or a new invitation replaces them.

## [0.3.1] - 2026-08-03

### Fixed

- Make the bundled Python package importable under MaiBot's isolated plugin loader.
- Enable the external NapCat plugin during Bridge installation.
- Prevent duplicate supervised AV Host processes during overlapping restarts.

## [0.3.0] - 2026-08-03

### Added

- Installable external NapCat AV bridge and separate QQ/Electron AVSDK host.
- Reversible, version-sensitive QQ loader hook with exact backup restoration.
- Isolated PulseAudio setup, launch scripts and authenticated diagnostics.
- Node tests for the verified Accept mapping, redaction and loopback-only configuration.

### Changed

- Generic `maibot_qq_speaker` and `maibot_qq_mic` default audio devices.
- Public metadata now uses the repository owner instead of deployment-specific naming.

## [0.2.0] - 2026-08-03

### Added

- Post-call cleanup for filler ASR, unfinished fragments, duplicate turns and control text.
- Evidence-grounded call summaries and caller facts through a configurable MaiBot model task.
- Silent writeback to the caller's private chat and immediate Maisaka context append.
- Runtime metrics for the latest memory write result and duration.

## [0.1.0] - 2026-07-30

### Added

- MaiBot 1.x third-party plugin lifecycle and typed WebUI configuration.
- External QQ AV bridge client with Bearer Token authentication.
- Realtime Qwen ASR and cloned Qwen3 TTS API providers.
- MaiBot person, chat, message, memory and model-task integration.
- Adaptive VAD, incomplete-turn merging, barge-in and soft-limited gain.
- Runtime status and private reply-test plugin APIs.

[Unreleased]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.4...HEAD
[0.3.4]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/releases/tag/v0.1.0
