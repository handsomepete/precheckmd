"""Operational Domain policies: deadline risk, idle gaps, conflict listing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from operational.events import TaskStatus
from operational.models import OperationalResource, OperationalTask
from operational.projection import OperationalProjection, Reservation


@dataclass
class DeadlineRisk:
    task_id: str
    task_name: str
    deadline: datetime
    scheduled_end: datetime | None
    minutes_over: float


@dataclass
class IdleGap:
    resource_id: str
    resource_name: str
    start: datetime
    end: datetime


@dataclass
class ConflictPair:
    resource_id: str
    a_task_id: str
    b_task_id: str
    overlap_start: datetime
    overlap_end: datetime


def deadline_risk_policy(
    projection: OperationalProjection,
    tasks: dict[str, OperationalTask],
) -> list[DeadlineRisk]:
    out: list[DeadlineRisk] = []
    for task_id, task in tasks.items():
        if task.deadline is None:
            continue
        state = projection.tasks.get(task_id)
        if state is None or state.status in (
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
        ):
            continue
        # Unscheduled tasks with a deadline that's already past = risk.
        if state.scheduled_end is None:
            if task.deadline <= datetime.now(timezone.utc):
                out.append(
                    DeadlineRisk(
                        task_id=task_id,
                        task_name=task.name,
                        deadline=task.deadline,
                        scheduled_end=None,
                        minutes_over=0.0,
                    )
                )
            continue
        if state.scheduled_end > task.deadline:
            over = (state.scheduled_end - task.deadline).total_seconds() / 60
            out.append(
                DeadlineRisk(
                    task_id=task_id,
                    task_name=task.name,
                    deadline=task.deadline,
                    scheduled_end=state.scheduled_end,
                    minutes_over=over,
                )
            )
    return out


def idle_gap_policy(
    projection: OperationalProjection,
    resources: dict[str, OperationalResource],
    *,
    window_start: datetime,
    window_end: datetime,
    minimum_gap_minutes: int = 30,
) -> list[IdleGap]:
    """List free intervals on each resource within [window_start, window_end]."""
    out: list[IdleGap] = []
    for resource_id, resource in resources.items():
        reservations = sorted(
            projection.reservations_for(resource_id), key=lambda r: r.start
        )
        cursor = window_start
        for r in reservations:
            if r.end <= window_start or r.start >= window_end:
                continue
            r_start = max(r.start, window_start)
            if (r_start - cursor) >= timedelta(minutes=minimum_gap_minutes):
                out.append(
                    IdleGap(
                        resource_id=resource_id,
                        resource_name=resource.name,
                        start=cursor,
                        end=r_start,
                    )
                )
            cursor = max(cursor, min(r.end, window_end))
        if (window_end - cursor) >= timedelta(minutes=minimum_gap_minutes):
            out.append(
                IdleGap(
                    resource_id=resource_id,
                    resource_name=resource.name,
                    start=cursor,
                    end=window_end,
                )
            )
    return out


def list_conflicts(
    projection: OperationalProjection,
    resources: dict[str, OperationalResource],
) -> list[ConflictPair]:
    """List all overlapping reservation pairs that exceed a resource's capacity.

    A resource with concurrent_capacity=N tolerates up to N overlapping
    reservations. Any pair that contributes to an over-capacity overlap is
    surfaced.
    """
    out: list[ConflictPair] = []
    for resource_id, resource in resources.items():
        cap = resource.concurrent_capacity or 1
        reservations = sorted(
            projection.reservations_for(resource_id), key=lambda r: r.start
        )
        for i, a in enumerate(reservations):
            overlapping = [a]
            for b in reservations[i + 1 :]:
                if b.start >= a.end:
                    break
                overlapping.append(b)
            if len(overlapping) > cap:
                # Emit each (a, b) pair that exceeds capacity.
                for b in overlapping[1:]:
                    out.append(
                        ConflictPair(
                            resource_id=resource_id,
                            a_task_id=a.task_id,
                            b_task_id=b.task_id,
                            overlap_start=max(a.start, b.start),
                            overlap_end=min(a.end, b.end),
                        )
                    )
    return out


def overlaps(a: Reservation, b: Reservation) -> bool:
    return a.start < b.end and b.start < a.end
