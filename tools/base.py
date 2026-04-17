"""Base Tool interface for OpenCLAW adapters.

A Tool handles one ``target`` (home_assistant, ssh, api) and dispatches to
the operation-specific handler. All tools return a ToolResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class ToolResult:
    ok: bool
    target: str
    operation: str
    data: Any = None
    error: str | None = None
    # Populated when the adapter is running in dry-run mode (no real I/O).
    dry_run: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    target: str

    async def execute(self, operation: str, parameters: dict) -> ToolResult:
        """Execute an operation and return its result."""


Handler = Callable[[dict], Awaitable[ToolResult]]
