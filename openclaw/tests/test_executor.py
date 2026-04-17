"""Tests for the OpenCLAW executor.

Uses an in-memory FakeSession (no real DB) and a FakeRegistry with
deterministic ToolResults to verify:

- successful plan: every action audited, outcomes marked success
- failed validation: actions are rejected (no tools invoked)
- stop-on-failure: first failure halts; remaining actions marked skipped
- audit log captures target/operation/risk/dry_run/error
"""

from __future__ import annotations

import asyncio

import pytest

from openclaw.audit import ActionAudit
from openclaw.executor import execute_plan
from tools.base import Tool, ToolResult
from tools.registry import ToolRegistry
from validator.risk import RiskLevel
from validator.validator import (
    ActionDecision,
    ValidationIssue,
    ValidationResult,
    ValidationCode,
)


class FakeSession:
    """Minimal stand-in for sqlalchemy.ext.asyncio.AsyncSession."""

    def __init__(self) -> None:
        self.added: list = []
        self.flushes: int = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        pass


class ScriptedTool:
    """Returns whatever ToolResult was pre-scripted for the (op, params) call."""

    def __init__(self, target: str, script: dict[str, ToolResult]) -> None:
        self.target = target
        self.script = script
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, operation: str, parameters: dict) -> ToolResult:
        self.calls.append((operation, parameters))
        return self.script.get(
            operation,
            ToolResult(
                ok=True,
                target=self.target,
                operation=operation,
                data={"ok": True},
            ),
        )


def _decision(action_id: str, risk: RiskLevel = RiskLevel.LOW) -> ActionDecision:
    return ActionDecision(
        action_id=action_id,
        risk=risk,
        approved=True,
        approval_required=False,
        issues=[],
    )


def _plan(*actions) -> dict:
    return {"actions": list(actions)}


def _passing(*ids) -> ValidationResult:
    return ValidationResult(decisions=[_decision(i) for i in ids], issues=[])


def _failing(*ids) -> ValidationResult:
    decs = []
    for i in ids:
        d = _decision(i)
        d.issues.append(
            ValidationIssue(
                code=ValidationCode.APPROVAL_REQUIRED,
                message="approval required",
                action_id=i,
            )
        )
        decs.append(d)
    return ValidationResult(decisions=decs, issues=[])


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------- happy path ----------


def test_execute_successful_plan():
    tool = ScriptedTool(
        target="ssh",
        script={
            "systemctl_status": ToolResult(
                ok=True, target="ssh", operation="systemctl_status",
                data={"active": True},
            ),
        },
    )
    registry = ToolRegistry({"ssh": tool})
    session = FakeSession()
    plan = _plan(
        {
            "id": "a1",
            "target": "ssh",
            "operation": "systemctl_status",
            "parameters": {"service": "home-assistant"},
            "expected_outcome": "ok",
        }
    )
    validation = _passing("a1")

    outcome = _run(execute_plan(session, plan, validation, registry=registry))

    assert outcome.ok is True
    assert outcome.rejected is False
    assert len(outcome.results) == 1
    assert outcome.results[0]["outcome"] == "success"
    assert outcome.results[0]["data"] == {"active": True}
    assert len(tool.calls) == 1

    audits = [o for o in session.added if isinstance(o, ActionAudit)]
    assert len(audits) == 1
    assert audits[0].outcome == "success"
    assert audits[0].risk_level == "low"
    assert audits[0].completed_at is not None


# ---------- rejected when validation failed ----------


def test_execute_refuses_when_validation_failed():
    tool = ScriptedTool("ssh", script={})
    registry = ToolRegistry({"ssh": tool})
    session = FakeSession()
    plan = _plan(
        {
            "id": "a1",
            "target": "ssh",
            "operation": "systemctl_restart",
            "parameters": {"service": "docker"},
            "expected_outcome": "restart",
        }
    )
    validation = _failing("a1")

    outcome = _run(execute_plan(session, plan, validation, registry=registry))

    assert outcome.ok is False
    assert outcome.rejected is True
    assert outcome.results[0]["outcome"] == "rejected"
    assert tool.calls == []  # tool NEVER invoked

    audits = [o for o in session.added if isinstance(o, ActionAudit)]
    assert len(audits) == 1
    assert audits[0].outcome == "rejected"
    assert "approval required" in (audits[0].error or "")


# ---------- stop on first failure ----------


def test_stop_on_failure_marks_remaining_skipped():
    tool = ScriptedTool(
        "ssh",
        script={
            "systemctl_status": ToolResult(
                ok=True, target="ssh", operation="systemctl_status",
                data={"active": True},
            ),
            "docker_ps": ToolResult(
                ok=False, target="ssh", operation="docker_ps",
                error="docker daemon unreachable",
            ),
            "journalctl_tail": ToolResult(
                ok=True, target="ssh", operation="journalctl_tail",
                data={"lines": []},
            ),
        },
    )
    registry = ToolRegistry({"ssh": tool})
    session = FakeSession()
    plan = _plan(
        {
            "id": "a1",
            "target": "ssh",
            "operation": "systemctl_status",
            "parameters": {"service": "home-assistant"},
            "expected_outcome": "ok",
        },
        {
            "id": "a2",
            "target": "ssh",
            "operation": "docker_ps",
            "parameters": {},
            "expected_outcome": "list",
        },
        {
            "id": "a3",
            "target": "ssh",
            "operation": "journalctl_tail",
            "parameters": {"unit": "home-assistant", "lines": 10},
            "expected_outcome": "tail",
        },
    )
    validation = _passing("a1", "a2", "a3")

    outcome = _run(execute_plan(session, plan, validation, registry=registry))

    assert outcome.ok is False
    outcomes = [r["outcome"] for r in outcome.results]
    assert outcomes == ["success", "failure", "skipped"]

    # Only the first two tool calls actually happened; a3 was skipped.
    called_ops = [op for op, _ in tool.calls]
    assert called_ops == ["systemctl_status", "docker_ps"]


# ---------- dry-run propagation ----------


def test_dry_run_result_recorded_in_audit():
    tool = ScriptedTool(
        "home_assistant",
        script={
            "get_state": ToolResult(
                ok=True, target="home_assistant", operation="get_state",
                data={"state": "on"}, dry_run=True,
            ),
        },
    )
    registry = ToolRegistry({"home_assistant": tool})
    session = FakeSession()
    plan = _plan(
        {
            "id": "a1",
            "target": "home_assistant",
            "operation": "get_state",
            "parameters": {"entity_id": "light.kitchen"},
            "expected_outcome": "on",
        }
    )
    validation = _passing("a1")

    outcome = _run(execute_plan(session, plan, validation, registry=registry))

    assert outcome.ok is True
    audits = [o for o in session.added if isinstance(o, ActionAudit)]
    assert audits[0].dry_run == 1
    assert outcome.results[0]["dry_run"] is True


# ---------- missing tool yields failure ----------


def test_missing_tool_target_fails_gracefully():
    registry = ToolRegistry({})  # no tools registered
    session = FakeSession()
    plan = _plan(
        {
            "id": "a1",
            "target": "ssh",
            "operation": "systemctl_status",
            "parameters": {"service": "home-assistant"},
            "expected_outcome": "ok",
        }
    )
    validation = _passing("a1")

    outcome = _run(execute_plan(session, plan, validation, registry=registry))

    assert outcome.ok is False
    assert outcome.results[0]["outcome"] == "failure"
    audits = [o for o in session.added if isinstance(o, ActionAudit)]
    assert "no tool registered" in (audits[0].error or "")
