"""SQLAlchemy model for the action audit log.

Every executed (or rejected) action is recorded here. Rows are immutable
after write; the ``outcome`` column transitions once from ``pending`` ->
``success|failure|rejected|skipped``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ActionAudit(Base):
    __tablename__ = "system_action_audit"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    plan_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_system_action_audit_plan_id", "plan_id"),
        Index("ix_system_action_audit_outcome", "outcome"),
    )
