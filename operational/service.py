"""Operational Domain service layer: validate-then-execute path for all writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from operational.constraints import (
    OperationalReport,
    evaluate_event,
    evaluate_state,
)
from operational.events import OperationalEventType
from operational.models import (
    OperationalEvent,
    OperationalResource,
    OperationalTask,
)
from operational.projection import (
    OperationalProjection,
    build_projection,
    load_resources,
    load_tasks,
)


class OperationalConstraintViolation(Exception):
    def __init__(self, report: OperationalReport) -> None:
        super().__init__(
            "operational constraint violation: "
            + "; ".join(v.message for v in report.violations)
        )
        self.report = report


@dataclass
class RecordOperationalEventInput:
    event_type: OperationalEventType
    task_id: str | None = None
    resource_id: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    metadata: dict[str, Any] | None = None
    occurred_at: datetime | None = None


async def record_event(
    session: AsyncSession, payload: RecordOperationalEventInput
) -> OperationalEvent:
    resources = await load_resources(session)
    tasks = await load_tasks(session)
    projection = await build_projection(session)

    candidate = OperationalEvent(
        event_type=payload.event_type.value,
        task_id=payload.task_id,
        resource_id=payload.resource_id,
        scheduled_start=payload.scheduled_start,
        scheduled_end=payload.scheduled_end,
        metadata_json=payload.metadata or {},
        occurred_at=payload.occurred_at or datetime.now(timezone.utc),
    )

    report = evaluate_event(projection, candidate, resources, tasks)
    if not report.ok:
        raise OperationalConstraintViolation(report)

    session.add(candidate)
    await session.flush()
    return candidate


async def current_state(
    session: AsyncSession,
) -> tuple[
    OperationalProjection,
    dict[str, OperationalResource],
    dict[str, OperationalTask],
    OperationalReport,
]:
    resources = await load_resources(session)
    tasks = await load_tasks(session)
    projection = await build_projection(session)
    report = evaluate_state(projection, resources, tasks)
    return projection, resources, tasks, report
