"""Home Assistant REST API adapter.

Operates against HA_URL with HA_TOKEN. When either env var is absent, the
adapter runs in dry-run mode: it returns a well-formed ToolResult describing
what would have been sent but makes no network call. Callers therefore do
not need to special-case absent configuration.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from tools.base import ToolResult


class HomeAssistantTool:
    target = "home_assistant"

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("HA_URL", "")).rstrip("/")
        self.token = token or os.getenv("HA_TOKEN", "")
        self._client = client
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    async def execute(self, operation: str, parameters: dict) -> ToolResult:
        if operation == "call_service":
            return await self._call_service(parameters)
        if operation == "get_state":
            return await self._get_state(parameters)
        return ToolResult(
            ok=False,
            target=self.target,
            operation=operation,
            error=f"unsupported operation {operation!r}",
        )

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if self._client is not None:
            resp = await self._client.request(
                method, f"{self.base_url}{path}", headers=headers, **kwargs
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method, f"{self.base_url}{path}", headers=headers, **kwargs
                )
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return resp.status_code, body

    async def _call_service(self, parameters: dict) -> ToolResult:
        domain = parameters.get("domain")
        service = parameters.get("service")
        service_data = parameters.get("service_data", {})
        if not self.configured:
            return ToolResult(
                ok=True,
                target=self.target,
                operation="call_service",
                data={"domain": domain, "service": service, "data": service_data},
                dry_run=True,
                meta={"reason": "HA_URL/HA_TOKEN not set"},
            )
        status, body = await self._request(
            "POST",
            f"/api/services/{domain}/{service}",
            json=service_data,
        )
        return ToolResult(
            ok=200 <= status < 300,
            target=self.target,
            operation="call_service",
            data=body,
            error=None if 200 <= status < 300 else f"HTTP {status}",
            meta={"status_code": status},
        )

    async def _get_state(self, parameters: dict) -> ToolResult:
        entity_id = parameters.get("entity_id")
        if not self.configured:
            return ToolResult(
                ok=True,
                target=self.target,
                operation="get_state",
                data={"entity_id": entity_id, "state": "unknown"},
                dry_run=True,
                meta={"reason": "HA_URL/HA_TOKEN not set"},
            )
        status, body = await self._request("GET", f"/api/states/{entity_id}")
        return ToolResult(
            ok=200 <= status < 300,
            target=self.target,
            operation="get_state",
            data=body,
            error=None if 200 <= status < 300 else f"HTTP {status}",
            meta={"status_code": status},
        )
