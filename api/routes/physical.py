"""Physical Domain HTTP routes.

All write paths funnel through ``physical.service`` so constraints are
evaluated before any event is persisted. A constraint violation surfaces as
HTTP 409 with the violation report in the body.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from api.deps import get_db
from financial.projection import available_budget as financial_available_budget
from physical.constraints import ConstraintReport
from physical.events import PhysicalEventType
from physical.models import (
    PhysicalItem,
    PhysicalProcurementRequest,
    PhysicalStorageNode,
)
from physical.policies import (
    capacity_policy,
    expiry_policy,
    reorder_policy,
)
from physical.service import (
    ConstraintViolation,
    RecordEventInput,
    approve_procurement,
    current_state,
    list_open_procurement,
    record_event,
    request_procurement,
)

router = APIRouter(
    prefix="/physical",
    tags=["physical"],
    dependencies=[Depends(require_api_key)],
)


# ---------- schemas ----------


class ItemIn(BaseModel):
    name: str
    category: str | None = None
    unit: str = "unit"
    reorder_threshold: float = 0
    critical_threshold: float = 0
    default_shelf_life_days: int | None = None
    unit_cost: float = 0


class ItemOut(BaseModel):
    id: str
    name: str
    category: str | None
    unit: str
    reorder_threshold: float
    critical_threshold: float
    default_shelf_life_days: int | None
    unit_cost: float

    @classmethod
    def from_model(cls, item: PhysicalItem) -> "ItemOut":
        return cls(
            id=item.id,
            name=item.name,
            category=item.category,
            unit=item.unit,
            reorder_threshold=float(item.reorder_threshold),
            critical_threshold=float(item.critical_threshold),
            default_shelf_life_days=item.default_shelf_life_days,
            unit_cost=float(item.unit_cost),
        )


class StorageNodeIn(BaseModel):
    name: str
    kind: str = "pantry"
    capacity_units: float | None = None
    temperature_c: float | None = None


class StorageNodeOut(BaseModel):
    id: str
    name: str
    kind: str
    capacity_units: float | None
    temperature_c: float | None

    @classmethod
    def from_model(cls, n: PhysicalStorageNode) -> "StorageNodeOut":
        return cls(
            id=n.id,
            name=n.name,
            kind=n.kind,
            capacity_units=float(n.capacity_units) if n.capacity_units is not None else None,
            temperature_c=float(n.temperature_c) if n.temperature_c is not None else None,
        )


class EventIn(BaseModel):
    event_type: PhysicalEventType
    item_id: str | None = None
    storage_node_id: str | None = None
    destination_node_id: str | None = None
    quantity: float = 0
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class EventOut(BaseModel):
    id: str
    event_type: str
    item_id: str | None
    storage_node_id: str | None
    destination_node_id: str | None
    quantity: float
    expires_at: datetime | None
    metadata: dict[str, Any]
    occurred_at: datetime


class ProcurementIn(BaseModel):
    item_id: str
    quantity: float
    reason: str | None = None
    available_budget: float | None = None


class ProcurementApproveIn(BaseModel):
    available_budget: float | None = None


class ProcurementOut(BaseModel):
    id: str
    item_id: str
    quantity: float
    estimated_cost: float
    reason: str | None
    approved: bool
    requested_at: datetime
    approved_at: datetime | None

    @classmethod
    def from_model(cls, r: PhysicalProcurementRequest) -> "ProcurementOut":
        return cls(
            id=r.id,
            item_id=r.item_id,
            quantity=float(r.quantity),
            estimated_cost=float(r.estimated_cost),
            reason=r.reason,
            approved=r.approved,
            requested_at=r.requested_at,
            approved_at=r.approved_at,
        )


class LotOut(BaseModel):
    item_id: str
    storage_node_id: str
    expires_at: datetime | None
    quantity: float


class StateOut(BaseModel):
    lots: list[LotOut]
    violations: list[dict[str, Any]]


# ---------- helpers ----------


def _violation_payload(report: ConstraintReport) -> dict[str, Any]:
    return {
        "violations": [
            {
                "code": v.code.value,
                "message": v.message,
                "item_id": v.item_id,
                "storage_node_id": v.storage_node_id,
            }
            for v in report.violations
        ]
    }


# ---------- items ----------


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
async def create_item(payload: ItemIn, db: AsyncSession = Depends(get_db)) -> ItemOut:
    item = PhysicalItem(
        name=payload.name,
        category=payload.category,
        unit=payload.unit,
        reorder_threshold=payload.reorder_threshold,
        critical_threshold=payload.critical_threshold,
        default_shelf_life_days=payload.default_shelf_life_days,
        unit_cost=payload.unit_cost,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return ItemOut.from_model(item)


@router.get("/items", response_model=list[ItemOut])
async def list_items(db: AsyncSession = Depends(get_db)) -> list[ItemOut]:
    result = await db.execute(select(PhysicalItem).order_by(PhysicalItem.name.asc()))
    return [ItemOut.from_model(i) for i in result.scalars()]


# ---------- storage nodes ----------


@router.post(
    "/storage-nodes",
    response_model=StorageNodeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_storage_node(
    payload: StorageNodeIn, db: AsyncSession = Depends(get_db)
) -> StorageNodeOut:
    node = PhysicalStorageNode(
        name=payload.name,
        kind=payload.kind,
        capacity_units=payload.capacity_units,
        temperature_c=payload.temperature_c,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return StorageNodeOut.from_model(node)


@router.get("/storage-nodes", response_model=list[StorageNodeOut])
async def list_storage_nodes(
    db: AsyncSession = Depends(get_db),
) -> list[StorageNodeOut]:
    result = await db.execute(
        select(PhysicalStorageNode).order_by(PhysicalStorageNode.name.asc())
    )
    return [StorageNodeOut.from_model(n) for n in result.scalars()]


# ---------- events (validate -> execute) ----------


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
async def submit_event(payload: EventIn, db: AsyncSession = Depends(get_db)) -> EventOut:
    try:
        event = await record_event(
            db,
            RecordEventInput(
                event_type=payload.event_type,
                item_id=payload.item_id,
                storage_node_id=payload.storage_node_id,
                destination_node_id=payload.destination_node_id,
                quantity=Decimal(str(payload.quantity)),
                expires_at=payload.expires_at,
                metadata=payload.metadata,
                occurred_at=payload.occurred_at,
            ),
        )
    except ConstraintViolation as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_violation_payload(exc.report),
        )
    await db.commit()
    return EventOut(
        id=event.id,
        event_type=event.event_type,
        item_id=event.item_id,
        storage_node_id=event.storage_node_id,
        destination_node_id=event.destination_node_id,
        quantity=float(event.quantity),
        expires_at=event.expires_at,
        metadata=event.metadata_json,
        occurred_at=event.occurred_at,
    )


# ---------- projection / current state ----------


@router.get("/state", response_model=StateOut)
async def get_state(db: AsyncSession = Depends(get_db)) -> StateOut:
    projection, _items, _nodes, report = await current_state(db)
    lots = [
        LotOut(
            item_id=lot.item_id,
            storage_node_id=lot.storage_node_id,
            expires_at=lot.expires_at,
            quantity=float(lot.quantity),
        )
        for lot in projection.non_empty_lots()
    ]
    return StateOut(lots=lots, violations=_violation_payload(report)["violations"])


@router.get("/recommendations")
async def get_recommendations(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    projection, items, nodes, _report = await current_state(db)
    reorders = reorder_policy(projection, items)
    expiries = expiry_policy(projection)
    capacity = capacity_policy(projection, nodes)
    return {
        "reorder": [
            {
                "item_id": r.item_id,
                "item_name": r.item_name,
                "current_quantity": float(r.current_quantity),
                "reorder_threshold": float(r.reorder_threshold),
                "recommended_quantity": float(r.recommended_quantity),
                "estimated_cost": float(r.estimated_cost),
                "reason": r.reason,
            }
            for r in reorders
        ],
        "expiry": [
            {
                "item_id": e.item_id,
                "storage_node_id": e.storage_node_id,
                "expires_at": e.expires_at.isoformat() if e.expires_at else None,
                "quantity": float(e.quantity),
                "horizon_days": e.horizon_days,
                "reason": e.reason,
            }
            for e in expiries
        ],
        "capacity": [
            {
                "storage_node_id": c.storage_node_id,
                "storage_node_name": c.storage_node_name,
                "used_units": float(c.used_units),
                "capacity_units": float(c.capacity_units),
                "utilization": c.utilization,
            }
            for c in capacity
        ],
    }


# ---------- procurement ----------


@router.post(
    "/procurement",
    response_model=ProcurementOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_procurement(
    payload: ProcurementIn, db: AsyncSession = Depends(get_db)
) -> ProcurementOut:
    budget = (
        Decimal(str(payload.available_budget))
        if payload.available_budget is not None
        else await financial_available_budget(db)
    )
    try:
        request = await request_procurement(
            db,
            item_id=payload.item_id,
            quantity=Decimal(str(payload.quantity)),
            reason=payload.reason,
            available_budget=budget,
        )
    except ConstraintViolation as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_violation_payload(exc.report),
        )
    await db.commit()
    return ProcurementOut.from_model(request)


@router.get("/procurement", response_model=list[ProcurementOut])
async def list_procurement(
    db: AsyncSession = Depends(get_db),
) -> list[ProcurementOut]:
    requests = await list_open_procurement(db)
    return [ProcurementOut.from_model(r) for r in requests]


@router.post("/procurement/{request_id}/approve", response_model=ProcurementOut)
async def approve_procurement_route(
    request_id: str,
    payload: ProcurementApproveIn,
    db: AsyncSession = Depends(get_db),
) -> ProcurementOut:
    budget = (
        Decimal(str(payload.available_budget))
        if payload.available_budget is not None
        else await financial_available_budget(db)
    )
    try:
        request = await approve_procurement(
            db,
            request_id=request_id,
            available_budget=budget,
        )
    except ConstraintViolation as exc:
        await db.rollback()
        if not exc.report.violations:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "procurement request not found"},
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_violation_payload(exc.report),
        )
    await db.commit()
    return ProcurementOut.from_model(request)
