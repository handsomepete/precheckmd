"""SQLAlchemy models for the Physical Domain.

Tables:
- physical_items: item catalog (canonical definitions)
- physical_storage_nodes: storage locations (pantry, fridge, freezer, ...)
- physical_inventory_events: append-only event log (source of truth)
- physical_procurement_requests: open / approved purchase requests

The current inventory state is derived by replaying inventory events; it is
never mutated directly. See physical/projection.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PhysicalItem(Base):
    """Catalog entry for an item that may exist in inventory."""

    __tablename__ = "physical_items"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="unit")
    # Reorder threshold (units). When projected quantity drops below this value
    # the reorder_policy emits a procurement request.
    reorder_threshold: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False, default=0
    )
    # Critical threshold: dropping below this is a hard constraint violation.
    critical_threshold: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False, default=0
    )
    # Default shelf life in days from time-of-add when no explicit expiry.
    default_shelf_life_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Estimated unit cost (used for procurement budgeting).
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_physical_items_category", "category"),)


class PhysicalStorageNode(Base):
    """A discrete storage location (e.g. pantry, fridge, freezer, garage)."""

    __tablename__ = "physical_storage_nodes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="pantry")
    # Optional capacity in units (None = unbounded).
    capacity_units: Mapped[float | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    temperature_c: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PhysicalInventoryEvent(Base):
    """Append-only event log. Projection defines truth."""

    __tablename__ = "physical_inventory_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("physical_items.id"),
        nullable=True,
    )
    storage_node_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("physical_storage_nodes.id"),
        nullable=True,
    )
    # For MOVE_ITEM: destination node.
    destination_node_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("physical_storage_nodes.id"),
        nullable=True,
    )
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    item: Mapped["PhysicalItem | None"] = relationship("PhysicalItem")

    __table_args__ = (
        Index("ix_physical_events_event_type", "event_type"),
        Index("ix_physical_events_item_id", "item_id"),
        Index("ix_physical_events_occurred_at", "occurred_at"),
    )


class PhysicalProcurementRequest(Base):
    """A procurement request (open or approved). Approval is a separate event."""

    __tablename__ = "physical_procurement_requests"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    item_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("physical_items.id"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    estimated_cost: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    item: Mapped["PhysicalItem"] = relationship("PhysicalItem")

    __table_args__ = (Index("ix_physical_proc_approved", "approved"),)
