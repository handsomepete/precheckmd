"""Physical Domain policies.

Policies inspect the current projection plus catalog data and emit
recommendations: reorder requests, expiry actions, capacity warnings. They do
not execute anything — they propose. Constraints (see constraints.py) decide
whether a proposed plan is admissible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from physical.models import PhysicalItem, PhysicalStorageNode
from physical.projection import InventoryProjection


@dataclass
class ReorderRecommendation:
    item_id: str
    item_name: str
    current_quantity: Decimal
    reorder_threshold: Decimal
    recommended_quantity: Decimal
    estimated_cost: Decimal
    reason: str


@dataclass
class ExpiryAction:
    item_id: str
    storage_node_id: str
    expires_at: datetime | None
    quantity: Decimal
    horizon_days: int
    reason: str


@dataclass
class CapacityWarning:
    storage_node_id: str
    storage_node_name: str
    used_units: Decimal
    capacity_units: Decimal
    utilization: float


def reorder_policy(
    projection: InventoryProjection,
    items: dict[str, PhysicalItem],
) -> list[ReorderRecommendation]:
    """Emit a ReorderRecommendation for each item below its reorder threshold.

    Recommended quantity refills to 2x reorder_threshold (a simple, predictable
    rule — policy may evolve, but determinism is preferred).
    """
    out: list[ReorderRecommendation] = []
    for item_id, item in items.items():
        threshold = Decimal(str(item.reorder_threshold or 0))
        current = projection.quantity(item_id)
        if threshold <= 0 or current >= threshold:
            continue
        target = threshold * Decimal("2")
        recommended = target - current
        cost = recommended * Decimal(str(item.unit_cost or 0))
        out.append(
            ReorderRecommendation(
                item_id=item_id,
                item_name=item.name,
                current_quantity=current,
                reorder_threshold=threshold,
                recommended_quantity=recommended,
                estimated_cost=cost,
                reason=(
                    f"projected qty {current} < reorder threshold {threshold}"
                ),
            )
        )
    return out


def expiry_policy(
    projection: InventoryProjection,
    *,
    horizon_days: int = 7,
    as_of: datetime | None = None,
) -> list[ExpiryAction]:
    """Emit ExpiryActions for lots already expired or expiring within the horizon."""
    as_of = as_of or datetime.now(timezone.utc)
    horizon = as_of + timedelta(days=horizon_days)
    out: list[ExpiryAction] = []
    for lot in projection.non_empty_lots():
        if lot.expires_at is None:
            continue
        if lot.expires_at <= as_of:
            reason = "expired"
        elif lot.expires_at <= horizon:
            reason = f"expires within {horizon_days}d"
        else:
            continue
        out.append(
            ExpiryAction(
                item_id=lot.item_id,
                storage_node_id=lot.storage_node_id,
                expires_at=lot.expires_at,
                quantity=lot.quantity,
                horizon_days=horizon_days,
                reason=reason,
            )
        )
    return out


def capacity_policy(
    projection: InventoryProjection,
    nodes: dict[str, PhysicalStorageNode],
    *,
    warn_at_utilization: float = 0.9,
) -> list[CapacityWarning]:
    """Emit a CapacityWarning for any node above the warning utilization."""
    out: list[CapacityWarning] = []
    for node_id, node in nodes.items():
        if node.capacity_units is None or Decimal(str(node.capacity_units)) <= 0:
            continue
        used = projection.node_total(node_id)
        cap = Decimal(str(node.capacity_units))
        utilization = float(used / cap) if cap > 0 else 0.0
        if utilization >= warn_at_utilization:
            out.append(
                CapacityWarning(
                    storage_node_id=node_id,
                    storage_node_name=node.name,
                    used_units=used,
                    capacity_units=cap,
                    utilization=utilization,
                )
            )
    return out
