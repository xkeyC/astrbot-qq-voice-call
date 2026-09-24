# Contributing

QQ/NapCat native behavior belongs behind the documented bridge boundary
(`bridge/PROTOCOL.md`); the AstrBot plugin only speaks that protocol.

Before submitting a change:

```bash
PYTHONPATH=/path/to/AstrBot python -m pytest tests -o asyncio_mode=auto
ruff check .
node --test bridge/tests/*.test.mjs
bash -n bridge/scripts/*.sh
```

Do not add generated audio, private voice samples, API keys, QQ credentials,
deployment-specific absolute paths or copied NapCat source.
