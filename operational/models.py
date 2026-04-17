"""SQLAlchemy models for the Operational Domain.

Tables:
- operational_resources: people, devices, rooms, vehicles, ...
- operational_tasks: discrete work items
- operational_events: append-only event log

Schedule and per-task status are derived. See operational/projection.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class OperationalResource(Base):
    __tablename__ = "operational_resources"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="person")
    # How many concurrent reservations the resource can hold (1 = exclusive).
    concurrent_capacity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OperationalTask(Base):
    __tablename__ = "operational_tasks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    duration_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # JSON array of resource ids required for this task to run.
    required_resource_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_operational_tasks_deadline", "deadline"),)


class OperationalEvent(Base):
    """Append-only event log."""

    __tablename__ = "operational_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("operational_tasks.id"),
        nullable=True,
    )
    resource_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("operational_resources.id"),
        nullable=True,
    )
    scheduled_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_operational_events_event_type", "event_type"),
        Index("ix_operational_events_task_id", "task_id"),
        Index("ix_operational_events_scheduled_start", "scheduled_start"),
    )
