"""In-process tests for projection, policies, and constraints.

These tests do not touch the database; they construct PhysicalInventoryEvent
instances and feed them through the pure functions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from physical.constraints import (
    ViolationCode,
    evaluate_event,
    evaluate_procurement_request,
    evaluate_state,
)
from physical.events import PhysicalEventType
from physical.models import (
    PhysicalInventoryEvent,
    PhysicalItem,
    PhysicalStorageNode,
)
from physical.policies import (
    capacity_policy,
    expiry_policy,
    reorder_policy,
)
from physical.projection import InventoryProjection, apply_event


def _ev(**kw) -> PhysicalInventoryEvent:
    kw.setdefault("metadata_json", {})
    kw.setdefault("occurred_at", datetime.now(timezone.utc))
    if isinstance(kw.get("event_type"), PhysicalEventType):
        kw["event_type"] = kw["event_type"].value
    return PhysicalInventoryEvent(**kw)


def _item(**kw) -> PhysicalItem:
    kw.setdefault("name", "milk")
    kw.setdefault("unit", "L")
    kw.setdefault("reorder_threshold", 0)
    kw.setdefault("critical_threshold", 0)
    kw.setdefault("unit_cost", 0)
    item = PhysicalItem(**kw)
    if "id" not in kw:
        item.id = "item-" + kw["name"]
    return item


def _node(**kw) -> PhysicalStorageNode:
    kw.setdefault("name", "fridge")
    kw.setdefault("kind", "fridge")
    node = PhysicalStorageNode(**kw)
    if "id" not in kw:
        node.id = "node-" + kw["name"]
    return node


def test_add_then_consume_projects_correct_quantity() -> None:
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("5"),
        ),
    )
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ITEM_CONSUMED,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("2"),
        ),
    )
    assert proj.quantity("i1") == Decimal("3")


def test_move_item_transfers_between_nodes() -> None:
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("4"),
        ),
    )
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.MOVE_ITEM,
            item_id="i1",
            storage_node_id="n1",
            destination_node_id="n2",
            quantity=Decimal("3"),
        ),
    )
    assert proj.quantity_at_node("i1", "n1") == Decimal("1")
    assert proj.quantity_at_node("i1", "n2") == Decimal("3")


def test_consume_uses_fefo_across_lots() -> None:
    proj = InventoryProjection()
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    later = datetime.now(timezone.utc) + timedelta(days=10)
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("2"),
            expires_at=later,
        ),
    )
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("2"),
            expires_at=soon,
        ),
    )
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ITEM_CONSUMED,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("3"),
        ),
    )
    # 2 from soon-expiring lot, then 1 from later. Soon lot should be empty.
    soon_lot = proj.lots[("i1", "n1", soon)]
    later_lot = proj.lots[("i1", "n1", later)]
    assert soon_lot.quantity == Decimal("0")
    assert later_lot.quantity == Decimal("1")


def test_oversell_yields_negative_inventory_violation() -> None:
    item = _item(name="milk", critical_threshold=0)
    node = _node(name="fridge")
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id=item.id,
            storage_node_id=node.id,
            quantity=Decimal("1"),
            expires_at=None,
        ),
    )
    candidate = _ev(
        event_type=PhysicalEventType.REMOVE_ITEM,
        item_id=item.id,
        storage_node_id=node.id,
        quantity=Decimal("5"),
    )
    report = evaluate_event(proj, candidate, {item.id: item}, {node.id: node})
    assert not report.ok
    assert any(v.code == ViolationCode.NEGATIVE_INVENTORY for v in report.violations)


def test_critical_depletion_blocks_consumption() -> None:
    item = _item(name="insulin", critical_threshold=Decimal("3"), unit_cost=Decimal("0"))
    node = _node(name="fridge")
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id=item.id,
            storage_node_id=node.id,
            quantity=Decimal("4"),
        ),
    )
    candidate = _ev(
        event_type=PhysicalEventType.ITEM_CONSUMED,
        item_id=item.id,
        storage_node_id=node.id,
        quantity=Decimal("2"),
    )
    report = evaluate_event(proj, candidate, {item.id: item}, {node.id: node})
    assert not report.ok
    assert any(v.code == ViolationCode.CRITICAL_DEPLETION for v in report.violations)


def test_capacity_exceeded_blocks_add() -> None:
    item = _item(name="bread")
    node = _node(name="bin", capacity_units=Decimal("10"))
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id=item.id,
            storage_node_id=node.id,
            quantity=Decimal("8"),
        ),
    )
    candidate = _ev(
        event_type=PhysicalEventType.ADD_ITEM,
        item_id=item.id,
        storage_node_id=node.id,
        quantity=Decimal("5"),
    )
    report = evaluate_event(proj, candidate, {item.id: item}, {node.id: node})
    assert not report.ok
    assert any(v.code == ViolationCode.CAPACITY_EXCEEDED for v in report.violations)


def test_expired_item_present_violation() -> None:
    item = _item(name="yogurt")
    node = _node(name="fridge")
    proj = InventoryProjection()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id=item.id,
            storage_node_id=node.id,
            quantity=Decimal("1"),
            expires_at=yesterday,
        ),
    )
    report = evaluate_state(proj, {item.id: item}, {node.id: node})
    assert not report.ok
    assert any(v.code == ViolationCode.EXPIRED_ITEM_PRESENT for v in report.violations)


def test_invalid_move_same_source_destination() -> None:
    item = _item(name="rice")
    node = _node(name="pantry")
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id=item.id,
            storage_node_id=node.id,
            quantity=Decimal("2"),
        ),
    )
    candidate = _ev(
        event_type=PhysicalEventType.MOVE_ITEM,
        item_id=item.id,
        storage_node_id=node.id,
        destination_node_id=node.id,
        quantity=Decimal("1"),
    )
    report = evaluate_event(proj, candidate, {item.id: item}, {node.id: node})
    assert any(v.code == ViolationCode.INVALID_MOVE for v in report.violations)


def test_unknown_item_or_node_rejected() -> None:
    candidate = _ev(
        event_type=PhysicalEventType.ADD_ITEM,
        item_id="ghost",
        storage_node_id="nowhere",
        quantity=Decimal("1"),
    )
    report = evaluate_event(InventoryProjection(), candidate, {}, {})
    codes = {v.code for v in report.violations}
    assert ViolationCode.UNKNOWN_ITEM in codes
    assert ViolationCode.UNKNOWN_STORAGE_NODE in codes


def test_reorder_policy_emits_when_below_threshold() -> None:
    item = _item(
        name="coffee",
        reorder_threshold=Decimal("4"),
        unit_cost=Decimal("12"),
    )
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id=item.id,
            storage_node_id="n1",
            quantity=Decimal("1"),
        ),
    )
    recs = reorder_policy(proj, {item.id: item})
    assert len(recs) == 1
    rec = recs[0]
    # target = 2 * threshold = 8; recommended = 8 - 1 = 7
    assert rec.recommended_quantity == Decimal("7")
    assert rec.estimated_cost == Decimal("84")


def test_expiry_policy_flags_expired_and_near_expiry() -> None:
    proj = InventoryProjection()
    now = datetime.now(timezone.utc)
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("1"),
            expires_at=now - timedelta(days=1),
        ),
    )
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id="n1",
            quantity=Decimal("1"),
            expires_at=now + timedelta(days=3),
        ),
    )
    actions = expiry_policy(proj, horizon_days=7, as_of=now)
    reasons = sorted(a.reason for a in actions)
    assert reasons == ["expired", "expires within 7d"]


def test_capacity_policy_warns_near_full() -> None:
    node = _node(name="bin", capacity_units=Decimal("10"))
    proj = InventoryProjection()
    apply_event(
        proj,
        _ev(
            event_type=PhysicalEventType.ADD_ITEM,
            item_id="i1",
            storage_node_id=node.id,
            quantity=Decimal("9.5"),
        ),
    )
    warnings = capacity_policy(proj, {node.id: node}, warn_at_utilization=0.9)
    assert len(warnings) == 1
    assert warnings[0].utilization >= 0.9


def test_financial_constraint_blocks_overbudget_procurement() -> None:
    report = evaluate_procurement_request(estimated_cost=120, available_budget=100)
    assert not report.ok
    assert report.violations[0].code == ViolationCode.FINANCIAL_BUDGET_EXCEEDED


def test_financial_constraint_passes_when_budget_unknown() -> None:
    report = evaluate_procurement_request(estimated_cost=120, available_budget=None)
    assert report.ok
