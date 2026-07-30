"""HTTP client for the external NapCat AV bridge."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from .config import BridgeSection


class BridgeClient:
    def __init__(self, session: ClientSession, config: BridgeSection) -> None:
        self._session = session
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {self._load_token()}"}

    def _load_token(self) -> str:
        token = os.getenv(self._config.token_env, "").strip()
        if token:
            return token
        if self._config.token_file:
            token_path = Path(self._config.token_file).expanduser()
            try:
                token = token_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RuntimeError(f"无法读取通话桥 Token 文件: {token_path}") from exc
        if not token:
            raise RuntimeError(
                f"通话桥 Token 为空；请设置 {self._config.token_env} 或 bridge.token_file"
            )
        return token

    async def current_call(self) -> dict[str, Any]:
        async with self._session.get(
            f"{self._base_url}/v1/calls/current",
            headers=self._headers,
            timeout=ClientTimeout(total=self._config.request_timeout_seconds),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}

    async def health(self) -> bool:
        try:
            await self.current_call()
        except Exception:
            return False
        return True
