"""Tool registry: maps action ``target`` -> Tool instance.

Constructed once per process (or per-request for tests) so OpenCLAW can
dispatch an action without caring which adapter backs it.
"""

from __future__ import annotations

from tools.api import ApiTool
from tools.base import Tool, ToolResult
from tools.home_assistant import HomeAssistantTool
from tools.ssh import SSHTool


class ToolRegistry:
    def __init__(self, tools: dict[str, Tool] | None = None) -> None:
        if tools is None:
            tools = {
                "home_assistant": HomeAssistantTool(),
                "ssh": SSHTool(),
                "api": ApiTool(),
            }
        self._tools = tools

    def register(self, tool: Tool) -> None:
        self._tools[tool.target] = tool

    def get(self, target: str) -> Tool | None:
        return self._tools.get(target)

    async def execute(
        self, target: str, operation: str, parameters: dict
    ) -> ToolResult:
        tool = self.get(target)
        if tool is None:
            return ToolResult(
                ok=False,
                target=target,
                operation=operation,
                error=f"no tool registered for target {target!r}",
            )
        return await tool.execute(operation, parameters)


default_registry = ToolRegistry()
