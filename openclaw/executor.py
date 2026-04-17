"""Sequential executor for validated action plans.

Rules (per the Validator spec):
- sequential only
- stop on failure
- full logging required

The executor is handed an already-validated plan plus a ValidationResult. If
the ValidationResult is not ok, the executor refuses to run any action and
records each rejected action in the audit log. Otherwise it walks the plan
one action at a time, dispatching to the ToolRegistry. Any action failure
stops the run; remaining actions are recorded as ``skipped``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from openclaw.audit import ActionAudit
from tools.base import ToolResult
from tools.registry import ToolRegistry, default_registry
from validator.validator import ActionDecision, ValidationResult


@dataclass
class ExecutionOutcome:
    plan_id: str
    ok: bool
    results: list[dict]  # serialized per-action outcomes
    rejected: bool = False


def _serialize(result: ToolResult | None, audit: ActionAudit) -> dict:
    return {
        "audit_id": audit.id,
        "action_id": audit.action_id,
        "target": audit.target,
        "operation": audit.operation,
        "outcome": audit.outcome,
        "risk_level": audit.risk_level,
        "dry_run": bool(audit.dry_run),
        "error": audit.error,
        "data": (result.data if result is not None else None),
    }


async def execute_plan(
    session: AsyncSession,
    plan: dict,
    validation: ValidationResult,
    *,
    registry: ToolRegistry | None = None,
    plan_id: str | None = None,
) -> ExecutionOutcome:
    """Execute a plan. Requires a passing ValidationResult."""
    registry = registry or default_registry
    plan_id = plan_id or str(uuid.uuid4())
    actions: list[dict] = plan.get("actions", [])
    decisions_by_id: dict[str, ActionDecision] = {
        d.action_id: d for d in validation.decisions
    }

    # Validation already failed — record each action as rejected and return.
    if not validation.ok:
        results: list[dict] = []
        for action in actions:
            decision = decisions_by_id.get(action.get("id", ""))
            audit = _audit_for(
                plan_id=plan_id,
                action=action,
                decision=decision,
                outcome="rejected",
                error="; ".join(
                    i.message for i in (decision.issues if decision else [])
                )
                or "validation failed",
            )
            session.add(audit)
            results.append(_serialize(None, audit))
        await session.flush()
        return ExecutionOutcome(
            plan_id=plan_id, ok=False, results=results, rejected=True
        )

    # Valid plan. Run sequentially. Stop on first failure.
    results: list[dict] = []
    stopped = False
    for action in actions:
        decision = decisions_by_id.get(action["id"])
        audit = _audit_for(
            plan_id=plan_id,
            action=action,
            decision=decision,
            outcome="pending",
        )
        session.add(audit)
        await session.flush()

        if stopped:
            audit.outcome = "skipped"
            audit.completed_at = datetime.now(timezone.utc)
            results.append(_serialize(None, audit))
            continue

        tool_result = await registry.execute(
            action["target"], action["operation"], action.get("parameters", {})
        )
        audit.outcome = "success" if tool_result.ok else "failure"
        audit.result = _jsonable(tool_result.data)
        audit.error = tool_result.error
        audit.dry_run = 1 if tool_result.dry_run else 0
        audit.completed_at = datetime.now(timezone.utc)
        results.append(_serialize(tool_result, audit))
        if not tool_result.ok:
            stopped = True

    await session.flush()
    return ExecutionOutcome(
        plan_id=plan_id,
        ok=not stopped,
        results=results,
        rejected=False,
    )


def _audit_for(
    *,
    plan_id: str,
    action: dict,
    decision: ActionDecision | None,
    outcome: str,
    error: str | None = None,
) -> ActionAudit:
    return ActionAudit(
        plan_id=plan_id,
        action_id=action.get("id", ""),
        target=action.get("target", ""),
        operation=action.get("operation", ""),
        parameters=action.get("parameters", {}),
        risk_level=(decision.risk.value if decision else action.get("risk_level", "low")),
        outcome=outcome,
        error=error,
    )


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of tool-returned data into something JSONB can store."""
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    try:
        return str(value)
    except Exception:
        return None
