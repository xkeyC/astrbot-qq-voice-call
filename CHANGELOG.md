# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Replace raw-VAD TTS interruption with two-stage VAD candidate detection and
  ASR-confirmed interruption commands or the current MaiBot nickname.

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

[Unreleased]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/releases/tag/v0.1.0
