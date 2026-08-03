# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ClaudiaGardner/maibot-qq-voice-call/releases/tag/v0.1.0
