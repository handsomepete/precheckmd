"""Financial Domain tables: accounts, obligations, events.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-17 00:00:01.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financial_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(64), nullable=False, server_default="checking"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column(
            "opening_balance", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "minimum_buffer", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "financial_obligations",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("financial_accounts.id"),
            nullable=False,
        ),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recurrence_days", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column(
            "cancelled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_financial_obligations_tier", "financial_obligations", ["tier"]
    )
    op.create_index(
        "ix_financial_obligations_due_date", "financial_obligations", ["due_date"]
    )
    op.create_index(
        "ix_financial_obligations_category", "financial_obligations", ["category"]
    )

    op.create_table(
        "financial_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("financial_accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "destination_account_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("financial_accounts.id"),
            nullable=True,
        ),
        sa.Column(
            "obligation_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("financial_obligations.id"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_financial_events_event_type", "financial_events", ["event_type"]
    )
    op.create_index(
        "ix_financial_events_account_id", "financial_events", ["account_id"]
    )
    op.create_index(
        "ix_financial_events_effective_at", "financial_events", ["effective_at"]
    )


def downgrade() -> None:
    op.drop_table("financial_events")
    op.drop_table("financial_obligations")
    op.drop_table("financial_accounts")
