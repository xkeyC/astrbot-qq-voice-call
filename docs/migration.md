# Migration from the private deployment

## Completed in 0.1.0

| Private implementation | Plugin implementation |
| --- | --- |
| Hard-coded `/home/...` paths | WebUI configuration and injected plugin paths |
| Read `MaiBot.db` directly | `ctx.person`, `ctx.chat`, `ctx.message` |
| Read `model_config.toml` for keys | Environment variables and `ctx.llm` |
| WebUI WebSocket chat emulation | `ctx.llm.generate` |
| Standalone health HTTP server | Public plugin status API |
| Production plus local TTS experiments | API-only realtime ASR/TTS |
| Deployment-specific constants | Generic configurable plugin |

The original production repository remains unchanged while this migration is
validated. It is not a source for the public Git history.

## Deliberately excluded

- API keys, QQ identifiers, person IDs and cloned-voice credentials
- Host-specific deployment paths and service scripts
- GDB/native tracing artifacts
- IndexTTS and GPT-SoVITS overlays, weights and benchmark outputs
- NapCat or QQ binaries

## Public bridge migration

Version 0.3.0 publishes the bridge source, reversible loader hook, audio setup,
diagnostics and tests. It deliberately does not redistribute QQ, NapCat or the
proprietary AVSDK library. The bridge is installed as an external NapCat plugin
under `plugins/napcat-plugin-maibot-qq-voice-call`; it no longer modifies the
built-in NapCat plugin.

The QQ loader hook is still version-sensitive because a second Electron process
must load the AVSDK shipped with the user's own QQ installation. Installation
backs up the original loader and uninstallation restores it byte-for-byte.

The manifest and plugin import have been validated against MaiBot `1.0.8` and
`maibot-plugin-sdk` `2.5.4`, matching the current private deployment.
