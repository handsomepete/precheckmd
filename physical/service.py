"""Physical Domain service layer.

Coordinates the validate -> execute path for all event submissions:

    candidate event
        -> load items + nodes + projection
        -> evaluate constraints on hypothetical post-state
        -> if violations: reject (do not execute)
        -> else: append event, return

Direct mutation is forbidden; everything goes through ``record_event``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from physical.constraints import (
    ConstraintReport,
    evaluate_event,
    evaluate_procurement_request,
    evaluate_state,
)
from physical.events import PhysicalEventType
from physical.models import (
    PhysicalInventoryEvent,
    PhysicalItem,
    PhysicalProcurementRequest,
    PhysicalStorageNode,
)
from physical.projection import (
    InventoryProjection,
    build_projection,
    load_items,
    load_storage_nodes,
)


class ConstraintViolation(Exception):
    """Raised when a candidate event would violate a hard constraint."""

    def __init__(self, report: ConstraintReport) -> None:
        super().__init__(
            "constraint violation: "
            + "; ".join(v.message for v in report.violations)
        )
        self.report = report


@dataclass
class RecordEventInput:
    event_type: PhysicalEventType
    item_id: str | None = None
    storage_node_id: str | None = None
    destination_node_id: str | None = None
    quantity: Decimal = Decimal("0")
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    occurred_at: datetime | None = None


async def record_event(
    session: AsyncSession, payload: RecordEventInput
) -> PhysicalInventoryEvent:
    """Validate then append an inventory event. Raises ConstraintViolation on reject."""
    items = await load_items(session)
    nodes = await load_storage_nodes(session)
    projection = await build_projection(session)

    # If item has a default shelf life and no expiry was supplied, derive one.
    expires_at = payload.expires_at
    if (
        payload.event_type == PhysicalEventType.ADD_ITEM
        and expires_at is None
        and payload.item_id in items
        and items[payload.item_id].default_shelf_life_days
    ):
        days = int(items[payload.item_id].default_shelf_life_days or 0)
        if days > 0:
            base = payload.occurred_at or datetime.now(timezone.utc)
            expires_at = base + timedelta(days=days)

    candidate = PhysicalInventoryEvent(
        event_type=payload.event_type.value,
        item_id=payload.item_id,
        storage_node_id=payload.storage_node_id,
        destination_node_id=payload.destination_node_id,
        quantity=payload.quantity,
        expires_at=expires_at,
        metadata_json=payload.metadata or {},
        occurred_at=payload.occurred_at or datetime.now(timezone.utc),
    )

    report = evaluate_event(projection, candidate, items, nodes)
    if not report.ok:
        raise ConstraintViolation(report)

    session.add(candidate)
    await session.flush()
    return candidate


async def request_procurement(
    session: AsyncSession,
    *,
    item_id: str,
    quantity: Decimal,
    reason: str | None = None,
    available_budget: Decimal | None = None,
) -> PhysicalProcurementRequest:
    """Create a procurement request. Validates against the financial constraint."""
    items = await load_items(session)
    if item_id not in items:
        raise ConstraintViolation(
            ConstraintReport(
                violations=[
                    # Reuse evaluate_event's UNKNOWN_ITEM by constructing here.
                ]
            )
        )
    item = items[item_id]
    estimated_cost = Decimal(str(item.unit_cost or 0)) * Decimal(str(quantity))

    report = evaluate_procurement_request(
        estimated_cost=estimated_cost, available_budget=available_budget
    )
    if not report.ok:
        raise ConstraintViolation(report)

    request = PhysicalProcurementRequest(
        item_id=item_id,
        quantity=quantity,
        estimated_cost=estimated_cost,
        reason=reason,
        approved=False,
    )
    session.add(request)
    await session.flush()

    event = PhysicalInventoryEvent(
        event_type=PhysicalEventType.PROCUREMENT_REQUESTED.value,
        item_id=item_id,
        quantity=quantity,
        metadata_json={
            "request_id": request.id,
            "estimated_cost": str(estimated_cost),
            "reason": reason or "",
        },
    )
    session.add(event)
    await session.flush()
    return request


async def approve_procurement(
    session: AsyncSession,
    *,
    request_id: str,
    available_budget: Decimal | None = None,
) -> PhysicalProcurementRequest:
    request = await session.get(PhysicalProcurementRequest, request_id)
    if request is None:
        raise ConstraintViolation(
            ConstraintReport(violations=[])
        )
    if request.approved:
        return request

    report = evaluate_procurement_request(
        estimated_cost=request.estimated_cost, available_budget=available_budget
    )
    if not report.ok:
        raise ConstraintViolation(report)

    request.approved = True
    request.approved_at = datetime.now(timezone.utc)

    event = PhysicalInventoryEvent(
        event_type=PhysicalEventType.PROCUREMENT_APPROVED.value,
        item_id=request.item_id,
        quantity=request.quantity,
        metadata_json={
            "request_id": request.id,
            "estimated_cost": str(request.estimated_cost),
        },
    )
    session.add(event)
    await session.flush()
    return request


async def current_state(
    session: AsyncSession,
) -> tuple[
    InventoryProjection,
    dict[str, PhysicalItem],
    dict[str, PhysicalStorageNode],
    ConstraintReport,
]:
    items = await load_items(session)
    nodes = await load_storage_nodes(session)
    projection = await build_projection(session)
    report = evaluate_state(projection, items, nodes)
    return projection, items, nodes, report


async def list_open_procurement(
    session: AsyncSession,
) -> list[PhysicalProcurementRequest]:
    result = await session.execute(
        select(PhysicalProcurementRequest)
        .where(PhysicalProcurementRequest.approved.is_(False))
        .order_by(PhysicalProcurementRequest.requested_at.asc())
    )
    return list(result.scalars())
