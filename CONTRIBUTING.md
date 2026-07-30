# Contributing

Keep changes platform-neutral on the MaiBot side. QQ/NapCat native behavior
belongs behind the documented bridge boundary.

Before submitting a change:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

Do not add model weights, generated audio, private voice samples, API keys,
QQ credentials, deployment-specific absolute paths or copied NapCat source.

New providers should implement the existing ASR/TTS boundaries and remain
optional. Production defaults must not start a local inference server.
