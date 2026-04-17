"""In-process tests for the Operational Domain (no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from operational.constraints import (
    OperationalViolationCode,
    evaluate_event,
    evaluate_state,
)
from operational.events import OperationalEventType, TaskStatus
from operational.models import (
    OperationalEvent,
    OperationalResource,
    OperationalTask,
)
from operational.policies import (
    deadline_risk_policy,
    idle_gap_policy,
    list_conflicts,
)
from operational.projection import OperationalProjection, apply_event


NOW = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)


def _resource(**kw) -> OperationalResource:
    kw.setdefault("name", "alice")
    kw.setdefault("kind", "person")
    kw.setdefault("concurrent_capacity", 1)
    r = OperationalResource(**kw)
    if "id" not in kw:
        r.id = "res-" + kw["name"]
    return r


def _task(**kw) -> OperationalTask:
    kw.setdefault("name", "laundry")
    kw.setdefault("priority", 3)
    kw.setdefault("duration_minutes", 60)
    kw.setdefault("deadline", None)
    kw.setdefault("required_resource_ids", [])
    kw.setdefault("description", None)
    t = OperationalTask(**kw)
    if "id" not in kw:
        t.id = "task-" + kw["name"]
    return t


def _event(**kw) -> OperationalEvent:
    kw.setdefault("metadata_json", {})
    kw.setdefault("occurred_at", NOW)
    if isinstance(kw.get("event_type"), OperationalEventType):
        kw["event_type"] = kw["event_type"].value
    return OperationalEvent(**kw)


def test_scheduling_a_task_records_window() -> None:
    proj = OperationalProjection()
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_CREATED,
            task_id="t1",
        ),
    )
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id="t1",
            scheduled_start=NOW,
            scheduled_end=NOW + timedelta(hours=1),
            metadata_json={"resource_ids": ["r1"]},
        ),
    )
    state = proj.tasks["t1"]
    assert state.status == TaskStatus.SCHEDULED
    assert state.resource_ids == ["r1"]


def test_resource_conflict_blocks_overlapping_schedule() -> None:
    r = _resource(name="oven", concurrent_capacity=1)
    t1 = _task(name="bake_a")
    t2 = _task(name="bake_b")
    proj = OperationalProjection()
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id=t1.id,
            scheduled_start=NOW,
            scheduled_end=NOW + timedelta(hours=2),
            metadata_json={"resource_ids": [r.id]},
        ),
    )
    candidate = _event(
        event_type=OperationalEventType.TASK_SCHEDULED,
        task_id=t2.id,
        scheduled_start=NOW + timedelta(hours=1),
        scheduled_end=NOW + timedelta(hours=3),
        metadata_json={"resource_ids": [r.id]},
    )
    report = evaluate_event(
        proj, candidate, {r.id: r}, {t1.id: t1, t2.id: t2}
    )
    assert not report.ok
    assert any(
        v.code == OperationalViolationCode.RESOURCE_CONFLICT
        for v in report.violations
    )


def test_resource_with_capacity_two_allows_overlap() -> None:
    r = _resource(name="conf-room", concurrent_capacity=2)
    t1 = _task(name="meet_a")
    t2 = _task(name="meet_b")
    proj = OperationalProjection()
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id=t1.id,
            scheduled_start=NOW,
            scheduled_end=NOW + timedelta(hours=1),
            metadata_json={"resource_ids": [r.id]},
        ),
    )
    candidate = _event(
        event_type=OperationalEventType.TASK_SCHEDULED,
        task_id=t2.id,
        scheduled_start=NOW + timedelta(minutes=15),
        scheduled_end=NOW + timedelta(hours=1, minutes=15),
        metadata_json={"resource_ids": [r.id]},
    )
    report = evaluate_event(
        proj, candidate, {r.id: r}, {t1.id: t1, t2.id: t2}
    )
    assert report.ok


def test_deadline_breach_blocks_schedule() -> None:
    r = _resource(name="alice")
    deadline = NOW + timedelta(hours=1)
    t = _task(name="taxes", deadline=deadline)
    proj = OperationalProjection()
    candidate = _event(
        event_type=OperationalEventType.TASK_SCHEDULED,
        task_id=t.id,
        scheduled_start=NOW,
        scheduled_end=NOW + timedelta(hours=2),
        metadata_json={"resource_ids": [r.id]},
    )
    report = evaluate_event(proj, candidate, {r.id: r}, {t.id: t})
    assert not report.ok
    assert any(
        v.code == OperationalViolationCode.DEADLINE_BREACH for v in report.violations
    )


def test_invalid_schedule_window_rejected() -> None:
    r = _resource(name="alice")
    t = _task(name="x")
    proj = OperationalProjection()
    candidate = _event(
        event_type=OperationalEventType.TASK_SCHEDULED,
        task_id=t.id,
        scheduled_start=NOW + timedelta(hours=2),
        scheduled_end=NOW + timedelta(hours=1),
        metadata_json={"resource_ids": [r.id]},
    )
    report = evaluate_event(proj, candidate, {r.id: r}, {t.id: t})
    assert any(
        v.code == OperationalViolationCode.INVALID_SCHEDULE_WINDOW
        for v in report.violations
    )


def test_missing_required_resource_rejected() -> None:
    needed = _resource(name="oven")
    other = _resource(name="alice")
    t = _task(name="bake", required_resource_ids=[needed.id])
    proj = OperationalProjection()
    candidate = _event(
        event_type=OperationalEventType.TASK_SCHEDULED,
        task_id=t.id,
        scheduled_start=NOW,
        scheduled_end=NOW + timedelta(hours=1),
        metadata_json={"resource_ids": [other.id]},
    )
    report = evaluate_event(
        proj, candidate, {needed.id: needed, other.id: other}, {t.id: t}
    )
    assert any(
        v.code == OperationalViolationCode.MISSING_RESOURCE_ASSIGNMENT
        for v in report.violations
    )


def test_cannot_start_unscheduled_task() -> None:
    r = _resource(name="alice")
    t = _task(name="x")
    proj = OperationalProjection()
    apply_event(
        proj, _event(event_type=OperationalEventType.TASK_CREATED, task_id=t.id)
    )
    candidate = _event(
        event_type=OperationalEventType.TASK_STARTED,
        task_id=t.id,
    )
    report = evaluate_event(proj, candidate, {r.id: r}, {t.id: t})
    assert any(
        v.code == OperationalViolationCode.INVALID_TRANSITION
        for v in report.violations
    )


def test_cannot_cancel_completed_task() -> None:
    r = _resource(name="alice")
    t = _task(name="x")
    proj = OperationalProjection()
    for et in (
        OperationalEventType.TASK_CREATED,
        OperationalEventType.TASK_SCHEDULED,
        OperationalEventType.TASK_STARTED,
        OperationalEventType.TASK_COMPLETED,
    ):
        ev = _event(event_type=et, task_id=t.id)
        if et == OperationalEventType.TASK_SCHEDULED:
            ev.scheduled_start = NOW
            ev.scheduled_end = NOW + timedelta(hours=1)
            ev.metadata_json = {"resource_ids": [r.id]}
        apply_event(proj, ev)
    assert proj.tasks[t.id].status == TaskStatus.COMPLETED
    candidate = _event(
        event_type=OperationalEventType.TASK_CANCELLED, task_id=t.id
    )
    report = evaluate_event(proj, candidate, {r.id: r}, {t.id: t})
    assert any(
        v.code == OperationalViolationCode.INVALID_TRANSITION
        for v in report.violations
    )


def test_unknown_task_or_resource_rejected() -> None:
    candidate = _event(
        event_type=OperationalEventType.TASK_SCHEDULED,
        task_id="ghost",
        scheduled_start=NOW,
        scheduled_end=NOW + timedelta(hours=1),
        metadata_json={"resource_ids": ["nope"]},
    )
    report = evaluate_event(OperationalProjection(), candidate, {}, {})
    codes = {v.code for v in report.violations}
    assert OperationalViolationCode.UNKNOWN_TASK in codes


def test_idle_gap_policy_finds_free_slots() -> None:
    r = _resource(name="alice")
    t = _task(name="x")
    proj = OperationalProjection()
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id=t.id,
            scheduled_start=NOW + timedelta(hours=2),
            scheduled_end=NOW + timedelta(hours=3),
            metadata_json={"resource_ids": [r.id]},
        ),
    )
    gaps = idle_gap_policy(
        proj,
        {r.id: r},
        window_start=NOW,
        window_end=NOW + timedelta(hours=8),
        minimum_gap_minutes=30,
    )
    # Should yield two gaps: [now..now+2h] and [now+3h..now+8h]
    assert len(gaps) == 2


def test_list_conflicts_surfaces_overlap() -> None:
    r = _resource(name="oven", concurrent_capacity=1)
    t1, t2 = _task(name="a"), _task(name="b")
    proj = OperationalProjection()
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id=t1.id,
            scheduled_start=NOW,
            scheduled_end=NOW + timedelta(hours=2),
            metadata_json={"resource_ids": [r.id]},
        ),
    )
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id=t2.id,
            scheduled_start=NOW + timedelta(hours=1),
            scheduled_end=NOW + timedelta(hours=3),
            metadata_json={"resource_ids": [r.id]},
        ),
    )
    conflicts = list_conflicts(proj, {r.id: r})
    assert len(conflicts) == 1


def test_deadline_risk_policy_flags_unscheduled_past_deadline() -> None:
    proj = OperationalProjection()
    t = _task(name="late", deadline=NOW - timedelta(days=1))
    apply_event(
        proj, _event(event_type=OperationalEventType.TASK_CREATED, task_id=t.id)
    )
    risks = deadline_risk_policy(proj, {t.id: t})
    assert len(risks) == 1
    assert risks[0].task_name == "late"


def test_state_passes_when_no_violations() -> None:
    r = _resource(name="alice")
    t = _task(name="laundry")
    proj = OperationalProjection()
    apply_event(
        proj, _event(event_type=OperationalEventType.TASK_CREATED, task_id=t.id)
    )
    apply_event(
        proj,
        _event(
            event_type=OperationalEventType.TASK_SCHEDULED,
            task_id=t.id,
            scheduled_start=NOW,
            scheduled_end=NOW + timedelta(hours=1),
            metadata_json={"resource_ids": [r.id]},
        ),
    )
    report = evaluate_state(proj, {r.id: r}, {t.id: t})
    assert report.ok
