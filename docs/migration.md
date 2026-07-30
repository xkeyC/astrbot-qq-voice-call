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
| Kaisy-specific constants | Generic configurable plugin |

The original production repository remains unchanged while this migration is
validated. It is not a source for the public Git history.

## Deliberately excluded

- API keys, QQ identifiers, person IDs and cloned-voice credentials
- Host-specific deployment paths and service scripts
- GDB/native tracing artifacts
- IndexTTS and GPT-SoVITS overlays, weights and benchmark outputs
- NapCat or QQ binaries

## Remaining work before public release

1. Deploy beside the existing orchestrator and compare call latency.
2. Cut over one test account, then remove the old DB/WebUI integration.
3. Decide whether the AV bridge can be published separately under NapCat's
   redistribution terms.
4. Open a MaiBot RFC for a planner-free, tool-capable realtime turn API.

The manifest and plugin import have been validated against MaiBot `1.0.8` and
`maibot-plugin-sdk` `2.5.4`, matching the current private deployment.
