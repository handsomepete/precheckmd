"""Generic HTTP GET adapter (read-only API probes)."""

from __future__ import annotations

from typing import Any

import httpx

from tools.base import ToolResult


class ApiTool:
    target = "api"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._client = client
        self._timeout = timeout

    async def execute(self, operation: str, parameters: dict) -> ToolResult:
        if operation != "get_state":
            return ToolResult(
                ok=False,
                target=self.target,
                operation=operation,
                error=f"unsupported operation {operation!r}",
            )
        url = parameters.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return ToolResult(
                ok=False,
                target=self.target,
                operation=operation,
                error="parameter 'url' must be http(s) URL",
            )
        headers: dict[str, Any] = parameters.get("headers", {}) or {}
        try:
            if self._client is not None:
                resp = await self._client.get(url, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                ok=False,
                target=self.target,
                operation=operation,
                error=f"request failed: {exc}",
            )
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return ToolResult(
            ok=200 <= resp.status_code < 300,
            target=self.target,
            operation=operation,
            data=body,
            error=None if 200 <= resp.status_code < 300 else f"HTTP {resp.status_code}",
            meta={"status_code": resp.status_code},
        )
