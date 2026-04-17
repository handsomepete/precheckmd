"""System-loop HTTP routes tying all six layers together.

Event → Projection → Constraints → Plan → Validate → Execute → Event

- GET  /system/state           aggregated snapshot across all three domains
- POST /system/plan            Claude planning layer: goal -> candidate plan
- POST /system/validate        Validator only (no execution)
- POST /system/execute         Validate + OpenCLAW execution (with approvals)
- POST /system/loop            Full loop: state -> plan -> validate -> execute
- GET  /system/audit           audit log (paginated by plan_id or latest)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.planner import PlannerError, plan as run_planner
from api.auth import require_api_key
from api.deps import get_db
from financial.projection import available_budget as financial_available_budget
from financial.service import current_state as financial_state
from openclaw.audit import ActionAudit
from openclaw.executor import execute_plan
from operational.service import current_state as operational_state
from physical.service import current_state as physical_state
from validator.validator import validate_plan

router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(require_api_key)],
)


# ---------- schemas ----------


class PlanIn(BaseModel):
    goal: str
    include_state: bool = True
    model: str = "claude-opus-4-7"


class ValidateIn(BaseModel):
    plan: dict[str, Any]
    approvals: list[str] = Field(default_factory=list)


class ExecuteIn(BaseModel):
    plan: dict[str, Any]
    approvals: list[str] = Field(default_factory=list)


class LoopIn(BaseModel):
    goal: str
    approvals: list[str] = Field(default_factory=list)
    model: str = "claude-opus-4-7"


# ---------- state aggregation ----------


async def _aggregate_state(db: AsyncSession) -> dict[str, Any]:
    ph_projection, ph_items, ph_nodes, ph_report = await physical_state(db)
    fi_projection, fi_accounts, fi_obligations, fi_report = await financial_state(db)
    op_projection, op_resources, op_tasks, op_report = await operational_state(db)
    budget = await financial_available_budget(db)
    return {
        "physical": {
            "lots": [
                {
                    "item_id": lot.item_id,
                    "storage_node_id": lot.storage_node_id,
                    "expires_at": lot.expires_at.isoformat() if lot.expires_at else None,
                    "quantity": float(lot.quantity),
                }
                for lot in ph_projection.non_empty_lots()
            ],
            "items": [
                {"id": i.id, "name": i.name, "unit": i.unit}
                for i in ph_items.values()
            ],
            "storage_nodes": [
                {"id": n.id, "name": n.name, "kind": n.kind}
                for n in ph_nodes.values()
            ],
            "violations": [
                {"code": v.code.value, "message": v.message}
                for v in ph_report.violations
            ],
        },
        "financial": {
            "balances": {k: float(v) for k, v in fi_projection.balances.items()},
            "accounts": [
                {"id": a.id, "name": a.name, "minimum_buffer": float(a.minimum_buffer)}
                for a in fi_accounts.values()
            ],
            "obligations": [
                {
                    "id": o.id,
                    "name": o.name,
                    "tier": o.tier,
                    "amount": float(o.amount),
                    "due_date": o.due_date.isoformat(),
                    "cancelled": o.cancelled,
                }
                for o in fi_obligations
            ],
            "available_budget": float(budget),
            "violations": [
                {"code": v.code.value, "message": v.message}
                for v in fi_report.violations
            ],
        },
        "operational": {
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status.value,
                    "scheduled_start": (
                        t.scheduled_start.isoformat() if t.scheduled_start else None
                    ),
                    "scheduled_end": (
                        t.scheduled_end.isoformat() if t.scheduled_end else None
                    ),
                    "resource_ids": list(t.resource_ids),
                }
                for t in op_projection.tasks.values()
            ],
            "resources": [
                {"id": r.id, "name": r.name, "kind": r.kind}
                for r in op_resources.values()
            ],
            "violations": [
                {"code": v.code.value, "message": v.message}
                for v in op_report.violations
            ],
        },
    }


def _serialize_validation(validation) -> dict[str, Any]:
    return {
        "ok": validation.ok,
        "issues": [
            {
                "code": i.code.value,
                "message": i.message,
                "action_id": i.action_id,
            }
            for i in validation.issues
        ],
        "decisions": [
            {
                "action_id": d.action_id,
                "risk": d.risk.value,
                "approval_required": d.approval_required,
                "approved": d.approved,
                "ok": d.ok,
                "issues": [
                    {
                        "code": i.code.value,
                        "message": i.message,
                        "action_id": i.action_id,
                    }
                    for i in d.issues
                ],
            }
            for d in validation.decisions
        ],
    }


# ---------- routes ----------


@router.get("/state")
async def get_state(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await _aggregate_state(db)


@router.post("/plan")
async def plan_route(
    payload: PlanIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    snapshot = await _aggregate_state(db) if payload.include_state else {}
    try:
        result = await run_planner(
            goal=payload.goal, state_snapshot=snapshot, model=payload.model
        )
    except PlannerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": str(exc)},
        )
    return {
        "plan": result.plan,
        "model": result.model,
        "dry_run": result.dry_run,
    }


@router.post("/validate")
async def validate_route(payload: ValidateIn) -> dict[str, Any]:
    validation = validate_plan(payload.plan, approvals=set(payload.approvals))
    return _serialize_validation(validation)


@router.post("/execute")
async def execute_route(
    payload: ExecuteIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    validation = validate_plan(payload.plan, approvals=set(payload.approvals))
    if not validation.ok:
        outcome = await execute_plan(db, payload.plan, validation)
        await db.commit()
        return {
            "validation": _serialize_validation(validation),
            "execution": {
                "plan_id": outcome.plan_id,
                "ok": outcome.ok,
                "rejected": outcome.rejected,
                "results": outcome.results,
            },
        }
    outcome = await execute_plan(db, payload.plan, validation)
    await db.commit()
    return {
        "validation": _serialize_validation(validation),
        "execution": {
            "plan_id": outcome.plan_id,
            "ok": outcome.ok,
            "rejected": outcome.rejected,
            "results": outcome.results,
        },
    }


@router.post("/loop")
async def loop_route(
    payload: LoopIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    snapshot = await _aggregate_state(db)
    try:
        planned = await run_planner(
            goal=payload.goal, state_snapshot=snapshot, model=payload.model
        )
    except PlannerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": str(exc)},
        )
    validation = validate_plan(planned.plan, approvals=set(payload.approvals))
    outcome = await execute_plan(db, planned.plan, validation)
    await db.commit()
    return {
        "plan": planned.plan,
        "planner": {"model": planned.model, "dry_run": planned.dry_run},
        "validation": _serialize_validation(validation),
        "execution": {
            "plan_id": outcome.plan_id,
            "ok": outcome.ok,
            "rejected": outcome.rejected,
            "results": outcome.results,
        },
    }


@router.get("/audit")
async def get_audit(
    db: AsyncSession = Depends(get_db),
    plan_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(ActionAudit).order_by(desc(ActionAudit.started_at)).limit(limit)
    if plan_id:
        stmt = stmt.where(ActionAudit.plan_id == plan_id)
    result = await db.execute(stmt)
    rows = list(result.scalars())
    return [
        {
            "id": r.id,
            "plan_id": r.plan_id,
            "action_id": r.action_id,
            "target": r.target,
            "operation": r.operation,
            "parameters": r.parameters,
            "risk_level": r.risk_level,
            "outcome": r.outcome,
            "result": r.result,
            "error": r.error,
            "dry_run": bool(r.dry_run),
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]
