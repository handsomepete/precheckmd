"""SSH adapter.

Dispatches the small set of read/log/restart operations allowed by the
Validator. No arbitrary command execution surface is exposed: each operation
maps to exactly one command template whose only variable component is a
parameter that was already validated by the Validator layer.

When SSH_HOST is not set, the adapter runs in dry-run mode and returns the
command that would have been run without attempting any connection.
"""

from __future__ import annotations

import asyncio
import os
import shlex

from tools.base import ToolResult


class SSHTool:
    target = "ssh"

    def __init__(
        self,
        host: str | None = None,
        user: str | None = None,
        key_path: str | None = None,
        *,
        port: int = 22,
        timeout: float = 10.0,
    ) -> None:
        self.host = host or os.getenv("SSH_HOST", "")
        self.user = user or os.getenv("SSH_USER", "")
        self.key_path = key_path or os.getenv("SSH_KEY", "")
        self.port = port
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user)

    async def execute(self, operation: str, parameters: dict) -> ToolResult:
        builder = _COMMAND_BUILDERS.get(operation)
        if builder is None:
            return ToolResult(
                ok=False,
                target=self.target,
                operation=operation,
                error=f"unsupported operation {operation!r}",
            )
        try:
            command = builder(parameters)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                target=self.target,
                operation=operation,
                error=str(exc),
            )

        if not self.configured:
            return ToolResult(
                ok=True,
                target=self.target,
                operation=operation,
                data={"command": command, "stdout": "", "stderr": ""},
                dry_run=True,
                meta={"reason": "SSH_HOST/SSH_USER not set"},
            )

        returncode, stdout, stderr = await self._run_ssh(command)
        return ToolResult(
            ok=returncode == 0,
            target=self.target,
            operation=operation,
            data={"command": command, "stdout": stdout, "stderr": stderr},
            error=None if returncode == 0 else f"exit {returncode}",
            meta={"returncode": returncode},
        )

    async def _run_ssh(self, remote_command: str) -> tuple[int, str, str]:
        ssh_argv = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={int(self.timeout)}",
            "-p",
            str(self.port),
        ]
        if self.key_path:
            ssh_argv += ["-i", self.key_path]
        ssh_argv.append(f"{self.user}@{self.host}")
        ssh_argv.append(remote_command)
        proc = await asyncio.create_subprocess_exec(
            *ssh_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout + 5
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return (124, "", "ssh timed out")
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )


# ---------- command builders (one per allowed operation) ----------


def _require_str(parameters: dict, key: str) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"parameter {key!r} is required and must be a non-empty string")
    return value


def _build_systemctl_status(parameters: dict) -> str:
    service = _require_str(parameters, "service")
    return f"systemctl status {shlex.quote(service)}"


def _build_systemctl_restart(parameters: dict) -> str:
    service = _require_str(parameters, "service")
    return f"sudo -n systemctl restart {shlex.quote(service)}"


def _build_docker_ps(parameters: dict) -> str:
    # No parameters. Fixed command.
    return "docker ps --format '{{json .}}'"


def _build_docker_logs(parameters: dict) -> str:
    container = _require_str(parameters, "container")
    tail = parameters.get("tail", 200)
    if not isinstance(tail, int) or tail <= 0 or tail > 10000:
        raise ValueError("parameter 'tail' must be 1..10000")
    return f"docker logs --tail {tail} {shlex.quote(container)}"


def _build_journalctl_tail(parameters: dict) -> str:
    unit = _require_str(parameters, "unit")
    lines = parameters.get("lines", 100)
    if not isinstance(lines, int) or lines <= 0 or lines > 10000:
        raise ValueError("parameter 'lines' must be 1..10000")
    return f"journalctl -u {shlex.quote(unit)} -n {lines} --no-pager"


def _build_read_file(parameters: dict) -> str:
    path = _require_str(parameters, "path")
    # read-only cat; 1MB ceiling so a bad path can't wedge a pipe.
    return f"head -c 1048576 {shlex.quote(path)}"


_COMMAND_BUILDERS = {
    "systemctl_status": _build_systemctl_status,
    "systemctl_restart": _build_systemctl_restart,
    "docker_ps": _build_docker_ps,
    "docker_logs": _build_docker_logs,
    "journalctl_tail": _build_journalctl_tail,
    "read_file": _build_read_file,
}
