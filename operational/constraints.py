"""Operational Domain hard constraints.

- No operational conflicts (resource overbooking beyond concurrent_capacity)
- Scheduled tasks must finish on or before deadline (when one is set)
- Required resources must exist
- Cannot start/complete a task that isn't scheduled
- TASK_SCHEDULED requires both start and end, with end > start
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum

from operational.events import OperationalEventType, TaskStatus
from operational.models import (
    OperationalEvent,
    OperationalResource,
    OperationalTask,
)
from operational.policies import list_conflicts
from operational.projection import OperationalProjection, apply_event


class OperationalViolationCode(str, Enum):
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    DEADLINE_BREACH = "DEADLINE_BREACH"
    UNKNOWN_TASK = "UNKNOWN_TASK"
    UNKNOWN_RESOURCE = "UNKNOWN_RESOURCE"
    INVALID_SCHEDULE_WINDOW = "INVALID_SCHEDULE_WINDOW"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    MISSING_RESOURCE_ASSIGNMENT = "MISSING_RESOURCE_ASSIGNMENT"


@dataclass
class OperationalViolation:
    code: OperationalViolationCode
    message: str
    task_id: str | None = None
    resource_id: str | None = None


@dataclass
class OperationalReport:
    violations: list[OperationalViolation]

    @property
    def ok(self) -> bool:
        return not self.violations


def _check_conflicts(
    projection: OperationalProjection,
    resources: dict[str, OperationalResource],
) -> list[OperationalViolation]:
    out: list[OperationalViolation] = []
    for c in list_conflicts(projection, resources):
        out.append(
            OperationalViolation(
                code=OperationalViolationCode.RESOURCE_CONFLICT,
                message=(
                    f"resource {c.resource_id} overbooked: tasks {c.a_task_id} "
                    f"and {c.b_task_id} overlap [{c.overlap_start} .. {c.overlap_end}]"
                ),
                task_id=c.b_task_id,
                resource_id=c.resource_id,
            )
        )
    return out


def _check_deadlines(
    projection: OperationalProjection,
    tasks: dict[str, OperationalTask],
) -> list[OperationalViolation]:
    out: list[OperationalViolation] = []
    for task_id, state in projection.tasks.items():
        if state.status not in (TaskStatus.SCHEDULED, TaskStatus.IN_PROGRESS):
            continue
        task = tasks.get(task_id)
        if task is None or task.deadline is None or state.scheduled_end is None:
            continue
        if state.scheduled_end > task.deadline:
            out.append(
                OperationalViolation(
                    code=OperationalViolationCode.DEADLINE_BREACH,
                    message=(
                        f"task '{task.name}' scheduled end {state.scheduled_end} "
                        f"is after deadline {task.deadline}"
                    ),
                    task_id=task_id,
                )
            )
    return out


def evaluate_state(
    projection: OperationalProjection,
    resources: dict[str, OperationalResource],
    tasks: dict[str, OperationalTask],
) -> OperationalReport:
    violations: list[OperationalViolation] = []
    violations.extend(_check_conflicts(projection, resources))
    violations.extend(_check_deadlines(projection, tasks))
    return OperationalReport(violations=violations)


def _structural_checks(
    event: OperationalEvent,
    resources: dict[str, OperationalResource],
    tasks: dict[str, OperationalTask],
    projection: OperationalProjection,
) -> list[OperationalViolation]:
    violations: list[OperationalViolation] = []
    if event.task_id is not None and event.task_id not in tasks:
        violations.append(
            OperationalViolation(
                code=OperationalViolationCode.UNKNOWN_TASK,
                message=f"unknown task_id {event.task_id}",
                task_id=event.task_id,
            )
        )
    if event.resource_id is not None and event.resource_id not in resources:
        violations.append(
            OperationalViolation(
                code=OperationalViolationCode.UNKNOWN_RESOURCE,
                message=f"unknown resource_id {event.resource_id}",
                resource_id=event.resource_id,
            )
        )
    if event.event_type in (
        OperationalEventType.TASK_SCHEDULED.value,
        OperationalEventType.TASK_RESCHEDULED.value,
    ):
        if not event.scheduled_start or not event.scheduled_end:
            violations.append(
                OperationalViolation(
                    code=OperationalViolationCode.INVALID_SCHEDULE_WINDOW,
                    message="scheduling requires scheduled_start and scheduled_end",
                    task_id=event.task_id,
                )
            )
        elif event.scheduled_end <= event.scheduled_start:
            violations.append(
                OperationalViolation(
                    code=OperationalViolationCode.INVALID_SCHEDULE_WINDOW,
                    message="scheduled_end must be after scheduled_start",
                    task_id=event.task_id,
                )
            )
        rids = (event.metadata_json or {}).get("resource_ids")
        if isinstance(rids, list):
            for rid in rids:
                if rid not in resources:
                    violations.append(
                        OperationalViolation(
                            code=OperationalViolationCode.UNKNOWN_RESOURCE,
                            message=f"unknown resource_id {rid} in metadata.resource_ids",
                            resource_id=rid,
                        )
                    )
            # Required resources for the task must be assigned.
            if event.task_id and event.task_id in tasks:
                required = set(tasks[event.task_id].required_resource_ids or [])
                if required and not required.issubset(set(rids)):
                    missing = sorted(required - set(rids))
                    violations.append(
                        OperationalViolation(
                            code=OperationalViolationCode.MISSING_RESOURCE_ASSIGNMENT,
                            message=(
                                f"task missing required resources: {missing}"
                            ),
                            task_id=event.task_id,
                        )
                    )
    if event.event_type == OperationalEventType.TASK_STARTED.value:
        state = projection.tasks.get(event.task_id) if event.task_id else None
        if state is None or state.status not in (
            TaskStatus.SCHEDULED,
            TaskStatus.IN_PROGRESS,
        ):
            violations.append(
                OperationalViolation(
                    code=OperationalViolationCode.INVALID_TRANSITION,
                    message="cannot start task that is not scheduled",
                    task_id=event.task_id,
                )
            )
    if event.event_type == OperationalEventType.TASK_COMPLETED.value:
        state = projection.tasks.get(event.task_id) if event.task_id else None
        if state is None or state.status not in (
            TaskStatus.SCHEDULED,
            TaskStatus.IN_PROGRESS,
        ):
            violations.append(
                OperationalViolation(
                    code=OperationalViolationCode.INVALID_TRANSITION,
                    message="cannot complete task that is not in progress or scheduled",
                    task_id=event.task_id,
                )
            )
    if event.event_type == OperationalEventType.TASK_CANCELLED.value:
        state = projection.tasks.get(event.task_id) if event.task_id else None
        if state and state.status == TaskStatus.COMPLETED:
            violations.append(
                OperationalViolation(
                    code=OperationalViolationCode.INVALID_TRANSITION,
                    message="cannot cancel a completed task",
                    task_id=event.task_id,
                )
            )
    return violations


def evaluate_event(
    projection: OperationalProjection,
    event: OperationalEvent,
    resources: dict[str, OperationalResource],
    tasks: dict[str, OperationalTask],
) -> OperationalReport:
    pre = _structural_checks(event, resources, tasks, projection)
    if pre:
        return OperationalReport(violations=pre)
    hypothetical = OperationalProjection(
        tasks={k: copy.copy(v) for k, v in projection.tasks.items()}
    )
    # Deep-copy resource_ids list (copy.copy is shallow on dataclasses).
    for k, v in hypothetical.tasks.items():
        hypothetical.tasks[k] = copy.copy(v)
        hypothetical.tasks[k].resource_ids = list(v.resource_ids)
    apply_event(hypothetical, event)
    return evaluate_state(hypothetical, resources, tasks)
