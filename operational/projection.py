"""Projection of the operational event log into the schedule.

A schedule consists of:
- Per-task: status (pending/scheduled/in_progress/completed/cancelled),
  scheduled_start, scheduled_end.
- Per-resource: list of (task_id, start, end) reservations from the latest
  active scheduling of each task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from operational.events import OperationalEventType, TaskStatus
from operational.models import (
    OperationalEvent,
    OperationalResource,
    OperationalTask,
)


@dataclass
class TaskState:
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    resource_ids: list[str] = field(default_factory=list)


@dataclass
class Reservation:
    task_id: str
    resource_id: str
    start: datetime
    end: datetime


@dataclass
class OperationalProjection:
    tasks: dict[str, TaskState] = field(default_factory=dict)

    def reservations(self) -> list[Reservation]:
        out: list[Reservation] = []
        for state in self.tasks.values():
            if (
                state.status in (TaskStatus.SCHEDULED, TaskStatus.IN_PROGRESS)
                and state.scheduled_start
                and state.scheduled_end
            ):
                for rid in state.resource_ids:
                    out.append(
                        Reservation(
                            task_id=state.task_id,
                            resource_id=rid,
                            start=state.scheduled_start,
                            end=state.scheduled_end,
                        )
                    )
        return out

    def reservations_for(self, resource_id: str) -> list[Reservation]:
        return [r for r in self.reservations() if r.resource_id == resource_id]


def apply_event(
    projection: OperationalProjection, event: OperationalEvent
) -> None:
    etype = event.event_type
    task_id = event.task_id
    if not task_id and etype != OperationalEventType.RESOURCE_REGISTERED.value:
        return

    if etype == OperationalEventType.TASK_CREATED.value:
        projection.tasks.setdefault(
            task_id, TaskState(task_id=task_id, status=TaskStatus.PENDING)
        )
        return

    state = projection.tasks.setdefault(task_id, TaskState(task_id=task_id))

    if etype in (
        OperationalEventType.TASK_SCHEDULED.value,
        OperationalEventType.TASK_RESCHEDULED.value,
    ):
        state.status = TaskStatus.SCHEDULED
        state.scheduled_start = event.scheduled_start
        state.scheduled_end = event.scheduled_end
        rids = event.metadata_json.get("resource_ids") if event.metadata_json else None
        if isinstance(rids, list):
            state.resource_ids = list(rids)
        elif event.resource_id:
            state.resource_ids = [event.resource_id]
    elif etype == OperationalEventType.TASK_STARTED.value:
        state.status = TaskStatus.IN_PROGRESS
    elif etype == OperationalEventType.TASK_COMPLETED.value:
        state.status = TaskStatus.COMPLETED
    elif etype == OperationalEventType.TASK_CANCELLED.value:
        state.status = TaskStatus.CANCELLED


async def build_projection(session: AsyncSession) -> OperationalProjection:
    stmt = select(OperationalEvent).order_by(
        OperationalEvent.occurred_at.asc(), OperationalEvent.id.asc()
    )
    result = await session.execute(stmt)
    projection = OperationalProjection()
    for event in result.scalars():
        apply_event(projection, event)
    return projection


async def load_resources(
    session: AsyncSession,
) -> dict[str, OperationalResource]:
    result = await session.execute(select(OperationalResource))
    return {r.id: r for r in result.scalars()}


async def load_tasks(session: AsyncSession) -> dict[str, OperationalTask]:
    result = await session.execute(select(OperationalTask))
    return {t.id: t for t in result.scalars()}
