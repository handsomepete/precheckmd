"""Operational Domain HTTP routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from api.deps import get_db
from operational.constraints import OperationalReport
from operational.events import OperationalEventType
from operational.models import (
    OperationalResource,
    OperationalTask,
)
from operational.policies import (
    deadline_risk_policy,
    idle_gap_policy,
    list_conflicts,
)
from operational.service import (
    OperationalConstraintViolation,
    RecordOperationalEventInput,
    current_state,
    record_event,
)

router = APIRouter(
    prefix="/operational",
    tags=["operational"],
    dependencies=[Depends(require_api_key)],
)


# ---------- schemas ----------


class ResourceIn(BaseModel):
    name: str
    kind: str = "person"
    concurrent_capacity: int = 1


class ResourceOut(BaseModel):
    id: str
    name: str
    kind: str
    concurrent_capacity: int

    @classmethod
    def from_model(cls, r: OperationalResource) -> "ResourceOut":
        return cls(
            id=r.id,
            name=r.name,
            kind=r.kind,
            concurrent_capacity=r.concurrent_capacity,
        )


class TaskIn(BaseModel):
    name: str
    priority: int = 3
    duration_minutes: int = 30
    deadline: datetime | None = None
    required_resource_ids: list[str] = Field(default_factory=list)
    description: str | None = None


class TaskOut(BaseModel):
    id: str
    name: str
    priority: int
    duration_minutes: int
    deadline: datetime | None
    required_resource_ids: list[str]
    description: str | None

    @classmethod
    def from_model(cls, t: OperationalTask) -> "TaskOut":
        return cls(
            id=t.id,
            name=t.name,
            priority=t.priority,
            duration_minutes=t.duration_minutes,
            deadline=t.deadline,
            required_resource_ids=list(t.required_resource_ids or []),
            description=t.description,
        )


class OperationalEventIn(BaseModel):
    event_type: OperationalEventType
    task_id: str | None = None
    resource_id: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class OperationalEventOut(BaseModel):
    id: str
    event_type: str
    task_id: str | None
    resource_id: str | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    metadata: dict[str, Any]
    occurred_at: datetime


class TaskStateOut(BaseModel):
    task_id: str
    status: str
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    resource_ids: list[str]


class StateOut(BaseModel):
    tasks: list[TaskStateOut]
    violations: list[dict[str, Any]]


# ---------- helpers ----------


def _violation_payload(report: OperationalReport) -> dict[str, Any]:
    return {
        "violations": [
            {
                "code": v.code.value,
                "message": v.message,
                "task_id": v.task_id,
                "resource_id": v.resource_id,
            }
            for v in report.violations
        ]
    }


# ---------- resources ----------


@router.post(
    "/resources",
    response_model=ResourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_resource(
    payload: ResourceIn, db: AsyncSession = Depends(get_db)
) -> ResourceOut:
    r = OperationalResource(**payload.model_dump())
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return ResourceOut.from_model(r)


@router.get("/resources", response_model=list[ResourceOut])
async def list_resources(db: AsyncSession = Depends(get_db)) -> list[ResourceOut]:
    result = await db.execute(
        select(OperationalResource).order_by(OperationalResource.name.asc())
    )
    return [ResourceOut.from_model(r) for r in result.scalars()]


# ---------- tasks ----------


@router.post(
    "/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED
)
async def create_task(
    payload: TaskIn, db: AsyncSession = Depends(get_db)
) -> TaskOut:
    t = OperationalTask(**payload.model_dump())
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return TaskOut.from_model(t)


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(db: AsyncSession = Depends(get_db)) -> list[TaskOut]:
    result = await db.execute(
        select(OperationalTask).order_by(OperationalTask.name.asc())
    )
    return [TaskOut.from_model(t) for t in result.scalars()]


# ---------- events ----------


@router.post(
    "/events",
    response_model=OperationalEventOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_event(
    payload: OperationalEventIn, db: AsyncSession = Depends(get_db)
) -> OperationalEventOut:
    try:
        event = await record_event(
            db,
            RecordOperationalEventInput(
                event_type=payload.event_type,
                task_id=payload.task_id,
                resource_id=payload.resource_id,
                scheduled_start=payload.scheduled_start,
                scheduled_end=payload.scheduled_end,
                metadata=payload.metadata,
                occurred_at=payload.occurred_at,
            ),
        )
    except OperationalConstraintViolation as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_violation_payload(exc.report),
        )
    await db.commit()
    return OperationalEventOut(
        id=event.id,
        event_type=event.event_type,
        task_id=event.task_id,
        resource_id=event.resource_id,
        scheduled_start=event.scheduled_start,
        scheduled_end=event.scheduled_end,
        metadata=event.metadata_json,
        occurred_at=event.occurred_at,
    )


# ---------- state / recommendations ----------


@router.get("/state", response_model=StateOut)
async def get_state(db: AsyncSession = Depends(get_db)) -> StateOut:
    projection, _resources, _tasks, report = await current_state(db)
    return StateOut(
        tasks=[
            TaskStateOut(
                task_id=s.task_id,
                status=s.status.value,
                scheduled_start=s.scheduled_start,
                scheduled_end=s.scheduled_end,
                resource_ids=list(s.resource_ids),
            )
            for s in projection.tasks.values()
        ],
        violations=_violation_payload(report)["violations"],
    )


@router.get("/recommendations")
async def get_recommendations(
    db: AsyncSession = Depends(get_db),
    horizon_hours: int = Query(168, ge=1, le=24 * 365),
) -> dict[str, Any]:
    projection, resources, tasks, _report = await current_state(db)
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=horizon_hours)
    return {
        "deadline_risk": [
            {
                "task_id": d.task_id,
                "task_name": d.task_name,
                "deadline": d.deadline.isoformat(),
                "scheduled_end": (
                    d.scheduled_end.isoformat() if d.scheduled_end else None
                ),
                "minutes_over": d.minutes_over,
            }
            for d in deadline_risk_policy(projection, tasks)
        ],
        "idle_gaps": [
            {
                "resource_id": g.resource_id,
                "resource_name": g.resource_name,
                "start": g.start.isoformat(),
                "end": g.end.isoformat(),
            }
            for g in idle_gap_policy(
                projection,
                resources,
                window_start=now,
                window_end=window_end,
            )
        ],
        "conflicts": [
            {
                "resource_id": c.resource_id,
                "a_task_id": c.a_task_id,
                "b_task_id": c.b_task_id,
                "overlap_start": c.overlap_start.isoformat(),
                "overlap_end": c.overlap_end.isoformat(),
            }
            for c in list_conflicts(projection, resources)
        ],
    }
