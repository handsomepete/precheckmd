"""Projection of physical inventory events into current state.

Projection defines truth: querying inventory means replaying the event log in
order. The system never stores or mutates derived state in place; all queries
return derived views.

A projected lot is keyed by (item_id, storage_node_id, expires_at).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from physical.events import PhysicalEventType
from physical.models import (
    PhysicalInventoryEvent,
    PhysicalItem,
    PhysicalStorageNode,
)

LotKey = tuple[str, str, datetime | None]


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class Lot:
    item_id: str
    storage_node_id: str
    expires_at: datetime | None
    quantity: Decimal = Decimal("0")


@dataclass
class InventoryProjection:
    lots: dict[LotKey, Lot] = field(default_factory=dict)

    def quantity(self, item_id: str) -> Decimal:
        total = Decimal("0")
        for key, lot in self.lots.items():
            if key[0] == item_id:
                total += lot.quantity
        return total

    def quantity_at_node(self, item_id: str, storage_node_id: str) -> Decimal:
        total = Decimal("0")
        for key, lot in self.lots.items():
            if key[0] == item_id and key[1] == storage_node_id:
                total += lot.quantity
        return total

    def node_total(self, storage_node_id: str) -> Decimal:
        total = Decimal("0")
        for key, lot in self.lots.items():
            if key[1] == storage_node_id:
                total += lot.quantity
        return total

    def expired_lots(self, as_of: datetime | None = None) -> list[Lot]:
        as_of = as_of or datetime.now(timezone.utc)
        out: list[Lot] = []
        for lot in self.lots.values():
            if lot.expires_at is not None and lot.expires_at <= as_of and lot.quantity > 0:
                out.append(lot)
        return out

    def non_empty_lots(self) -> list[Lot]:
        return [lot for lot in self.lots.values() if lot.quantity > 0]


def _key(item_id: str, storage_node_id: str, expires_at: datetime | None) -> LotKey:
    return (item_id, storage_node_id, expires_at)


def apply_event(
    projection: InventoryProjection, event: PhysicalInventoryEvent
) -> None:
    """Mutate the projection in-place by applying a single event."""
    etype = event.event_type
    qty = _to_decimal(event.quantity)

    if etype == PhysicalEventType.ADD_ITEM:
        if not event.item_id or not event.storage_node_id:
            return
        key = _key(event.item_id, event.storage_node_id, event.expires_at)
        lot = projection.lots.setdefault(
            key,
            Lot(
                item_id=event.item_id,
                storage_node_id=event.storage_node_id,
                expires_at=event.expires_at,
            ),
        )
        lot.quantity += qty
        return

    if etype in (
        PhysicalEventType.REMOVE_ITEM,
        PhysicalEventType.ITEM_CONSUMED,
        PhysicalEventType.ITEM_EXPIRED,
    ):
        if not event.item_id or not event.storage_node_id:
            return
        # Remove from a specific lot when expires_at is provided; otherwise
        # consume across lots in expiry-soonest-first order (FEFO).
        if event.expires_at is not None:
            key = _key(event.item_id, event.storage_node_id, event.expires_at)
            lot = projection.lots.get(key)
            if lot is not None:
                lot.quantity -= qty
            return
        remaining = qty
        candidates = [
            lot
            for k, lot in projection.lots.items()
            if k[0] == event.item_id and k[1] == event.storage_node_id and lot.quantity > 0
        ]
        # FEFO: lots with no expiry come last (treated as +infinity).
        candidates.sort(key=lambda l: (l.expires_at is None, l.expires_at or datetime.max))
        for lot in candidates:
            if remaining <= 0:
                break
            take = min(lot.quantity, remaining)
            lot.quantity -= take
            remaining -= take
        # Any unsatisfied demand becomes a negative-quantity sentinel lot so
        # the constraint layer detects oversell. Direct mutation in projected
        # state remains forbidden — this is part of replay, not a write.
        if remaining > 0:
            sentinel_key = _key(event.item_id, event.storage_node_id, None)
            sentinel = projection.lots.setdefault(
                sentinel_key,
                Lot(
                    item_id=event.item_id,
                    storage_node_id=event.storage_node_id,
                    expires_at=None,
                ),
            )
            sentinel.quantity -= remaining
        return

    if etype == PhysicalEventType.MOVE_ITEM:
        if (
            not event.item_id
            or not event.storage_node_id
            or not event.destination_node_id
        ):
            return
        src_key = _key(event.item_id, event.storage_node_id, event.expires_at)
        dst_key = _key(event.item_id, event.destination_node_id, event.expires_at)
        src = projection.lots.get(src_key)
        if src is None:
            return
        moved = min(src.quantity, qty)
        src.quantity -= moved
        dst = projection.lots.setdefault(
            dst_key,
            Lot(
                item_id=event.item_id,
                storage_node_id=event.destination_node_id,
                expires_at=event.expires_at,
            ),
        )
        dst.quantity += moved
        return

    # PROCUREMENT_* events do not affect inventory directly. They become
    # ADD_ITEM events when goods physically arrive.
    return


async def build_projection(
    session: AsyncSession,
    *,
    as_of: datetime | None = None,
) -> InventoryProjection:
    """Replay the event log up to ``as_of`` (inclusive) and return the projection."""
    stmt = select(PhysicalInventoryEvent).order_by(
        PhysicalInventoryEvent.occurred_at.asc(),
        PhysicalInventoryEvent.id.asc(),
    )
    if as_of is not None:
        stmt = stmt.where(PhysicalInventoryEvent.occurred_at <= as_of)
    result = await session.execute(stmt)
    projection = InventoryProjection()
    for event in result.scalars():
        apply_event(projection, event)
    return projection


async def load_items(session: AsyncSession) -> dict[str, PhysicalItem]:
    result = await session.execute(select(PhysicalItem))
    return {item.id: item for item in result.scalars()}


async def load_storage_nodes(session: AsyncSession) -> dict[str, PhysicalStorageNode]:
    result = await session.execute(select(PhysicalStorageNode))
    return {node.id: node for node in result.scalars()}
