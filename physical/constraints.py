"""Physical Domain hard constraints.

Constraints are the enforcement layer for the Physical Domain. They evaluate a
projected state (current or hypothetical, after a candidate event) and report
any violations. The validator must reject any plan that yields a projected
state with violations.

Hard constraints (per HomeOS spec):
- no critical inventory depletion
- procurement must respect financial constraints
- expired items cannot exist in valid projected state

Plus structural invariants enforced here as part of "no operational conflicts":
- inventory quantities must be non-negative (no oversell)
- moves must have a different source and destination
- storage capacity must not be exceeded
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from physical.events import PhysicalEventType
from physical.models import (
    PhysicalInventoryEvent,
    PhysicalItem,
    PhysicalStorageNode,
)
from physical.projection import InventoryProjection, apply_event


class ViolationCode(str, Enum):
    NEGATIVE_INVENTORY = "NEGATIVE_INVENTORY"
    CRITICAL_DEPLETION = "CRITICAL_DEPLETION"
    EXPIRED_ITEM_PRESENT = "EXPIRED_ITEM_PRESENT"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    FINANCIAL_BUDGET_EXCEEDED = "FINANCIAL_BUDGET_EXCEEDED"
    INVALID_MOVE = "INVALID_MOVE"
    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    UNKNOWN_STORAGE_NODE = "UNKNOWN_STORAGE_NODE"


@dataclass
class Violation:
    code: ViolationCode
    message: str
    item_id: str | None = None
    storage_node_id: str | None = None


@dataclass
class ConstraintReport:
    violations: list[Violation]

    @property
    def ok(self) -> bool:
        return not self.violations


def _check_negative_and_critical(
    projection: InventoryProjection,
    items: dict[str, PhysicalItem],
) -> list[Violation]:
    out: list[Violation] = []
    # Negative inventory anywhere = invariant violation.
    for key, lot in projection.lots.items():
        if lot.quantity < 0:
            out.append(
                Violation(
                    code=ViolationCode.NEGATIVE_INVENTORY,
                    message=(
                        f"item {lot.item_id} at node {lot.storage_node_id} "
                        f"has negative quantity {lot.quantity}"
                    ),
                    item_id=lot.item_id,
                    storage_node_id=lot.storage_node_id,
                )
            )
    # Critical depletion: aggregate quantity below critical threshold.
    for item_id, item in items.items():
        critical = Decimal(str(item.critical_threshold or 0))
        if critical <= 0:
            continue
        total = projection.quantity(item_id)
        if total < critical:
            out.append(
                Violation(
                    code=ViolationCode.CRITICAL_DEPLETION,
                    message=(
                        f"item '{item.name}' qty {total} below critical "
                        f"threshold {critical}"
                    ),
                    item_id=item_id,
                )
            )
    return out


def _check_expired(
    projection: InventoryProjection, *, as_of: datetime | None = None
) -> list[Violation]:
    out: list[Violation] = []
    for lot in projection.expired_lots(as_of=as_of):
        out.append(
            Violation(
                code=ViolationCode.EXPIRED_ITEM_PRESENT,
                message=(
                    f"expired lot of item {lot.item_id} at node "
                    f"{lot.storage_node_id} (expired {lot.expires_at})"
                ),
                item_id=lot.item_id,
                storage_node_id=lot.storage_node_id,
            )
        )
    return out


def _check_capacity(
    projection: InventoryProjection,
    nodes: dict[str, PhysicalStorageNode],
) -> list[Violation]:
    out: list[Violation] = []
    for node_id, node in nodes.items():
        if node.capacity_units is None:
            continue
        cap = Decimal(str(node.capacity_units))
        if cap <= 0:
            continue
        used = projection.node_total(node_id)
        if used > cap:
            out.append(
                Violation(
                    code=ViolationCode.CAPACITY_EXCEEDED,
                    message=(
                        f"storage '{node.name}' over capacity: {used} > {cap}"
                    ),
                    storage_node_id=node_id,
                )
            )
    return out


def evaluate_state(
    projection: InventoryProjection,
    items: dict[str, PhysicalItem],
    nodes: dict[str, PhysicalStorageNode],
    *,
    as_of: datetime | None = None,
) -> ConstraintReport:
    """Evaluate a (current or hypothetical) projected state against all hard constraints."""
    violations: list[Violation] = []
    violations.extend(_check_negative_and_critical(projection, items))
    violations.extend(_check_expired(projection, as_of=as_of))
    violations.extend(_check_capacity(projection, nodes))
    return ConstraintReport(violations=violations)


def evaluate_event(
    projection: InventoryProjection,
    event: PhysicalInventoryEvent,
    items: dict[str, PhysicalItem],
    nodes: dict[str, PhysicalStorageNode],
    *,
    as_of: datetime | None = None,
) -> ConstraintReport:
    """Validate a candidate event by simulating it on a copy of the projection.

    Returns a report describing any constraint violations that would result.
    The caller decides whether to accept (write the event) or reject.
    """
    pre_violations: list[Violation] = []

    # Structural / referential checks first.
    if event.item_id is not None and event.item_id not in items:
        pre_violations.append(
            Violation(
                code=ViolationCode.UNKNOWN_ITEM,
                message=f"unknown item_id {event.item_id}",
                item_id=event.item_id,
            )
        )
    for node_id in (event.storage_node_id, event.destination_node_id):
        if node_id is not None and node_id not in nodes:
            pre_violations.append(
                Violation(
                    code=ViolationCode.UNKNOWN_STORAGE_NODE,
                    message=f"unknown storage_node_id {node_id}",
                    storage_node_id=node_id,
                )
            )
    if event.event_type == PhysicalEventType.MOVE_ITEM:
        if (
            event.storage_node_id is not None
            and event.destination_node_id is not None
            and event.storage_node_id == event.destination_node_id
        ):
            pre_violations.append(
                Violation(
                    code=ViolationCode.INVALID_MOVE,
                    message="MOVE_ITEM source and destination are identical",
                )
            )

    if pre_violations:
        return ConstraintReport(violations=pre_violations)

    hypothetical = InventoryProjection(
        lots={k: copy.copy(v) for k, v in projection.lots.items()}
    )
    apply_event(hypothetical, event)
    return evaluate_state(hypothetical, items, nodes, as_of=as_of)


def evaluate_procurement_request(
    *,
    estimated_cost: Decimal | float | int,
    available_budget: Decimal | float | int | None,
) -> ConstraintReport:
    """Hard financial constraint: a procurement cannot exceed available budget.

    ``available_budget=None`` means the financial domain has not provided a
    budget; in that case we conservatively pass — the financial domain will
    enforce its own constraints when integrated.
    """
    if available_budget is None:
        return ConstraintReport(violations=[])
    cost = Decimal(str(estimated_cost))
    budget = Decimal(str(available_budget))
    if cost > budget:
        return ConstraintReport(
            violations=[
                Violation(
                    code=ViolationCode.FINANCIAL_BUDGET_EXCEEDED,
                    message=(
                        f"procurement cost {cost} exceeds available budget {budget}"
                    ),
                )
            ]
        )
    return ConstraintReport(violations=[])


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
