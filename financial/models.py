"""SQLAlchemy models for the Financial Domain.

Tables:
- financial_accounts: account catalog (checking, savings, credit, ...)
- financial_obligations: scheduled obligations (recurring or one-shot)
- financial_events: append-only event log

Balances and projected cashflow are derived (see financial/projection.py).
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
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from db.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class FinancialAccount(Base):
    __tablename__ = "financial_accounts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="checking")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    opening_balance: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    # Minimum balance buffer below which liquidity is treated as breached.
    minimum_buffer: Mapped[float] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FinancialObligation(Base):
    __tablename__ = "financial_obligations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Tier-1 = hard constraint; Tier-2/3 = informational.
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("financial_accounts.id"),
        nullable=False,
    )
    due_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # If non-null, obligation recurs every N days from due_date.
    recurrence_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_financial_obligations_tier", "tier"),
        Index("ix_financial_obligations_due_date", "due_date"),
        Index("ix_financial_obligations_category", "category"),
    )


class FinancialEvent(Base):
    """Append-only event log."""

    __tablename__ = "financial_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("financial_accounts.id"),
        nullable=True,
    )
    # For TRANSFER events: destination account.
    destination_account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("financial_accounts.id"),
        nullable=True,
    )
    obligation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("financial_obligations.id"),
        nullable=True,
    )
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    # When the cash movement actually occurs (may be in the future for plans).
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_financial_events_event_type", "event_type"),
        Index("ix_financial_events_account_id", "account_id"),
        Index("ix_financial_events_effective_at", "effective_at"),
    )
