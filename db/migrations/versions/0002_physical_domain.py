"""Physical Domain tables: items, storage nodes, inventory events, procurement.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "physical_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False, server_default="unit"),
        sa.Column(
            "reorder_threshold", sa.Numeric(12, 3), nullable=False, server_default="0"
        ),
        sa.Column(
            "critical_threshold", sa.Numeric(12, 3), nullable=False, server_default="0"
        ),
        sa.Column("default_shelf_life_days", sa.Integer(), nullable=True),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_physical_items_category", "physical_items", ["category"])

    op.create_table(
        "physical_storage_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(64), nullable=False, server_default="pantry"),
        sa.Column("capacity_units", sa.Numeric(12, 3), nullable=True),
        sa.Column("temperature_c", sa.Numeric(6, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "physical_inventory_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("physical_items.id"),
            nullable=True,
        ),
        sa.Column(
            "storage_node_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("physical_storage_nodes.id"),
            nullable=True,
        ),
        sa.Column(
            "destination_node_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("physical_storage_nodes.id"),
            nullable=True,
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_physical_events_event_type", "physical_inventory_events", ["event_type"]
    )
    op.create_index(
        "ix_physical_events_item_id", "physical_inventory_events", ["item_id"]
    )
    op.create_index(
        "ix_physical_events_occurred_at",
        "physical_inventory_events",
        ["occurred_at"],
    )

    op.create_table(
        "physical_procurement_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("physical_items.id"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column(
            "estimated_cost", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_physical_proc_approved", "physical_procurement_requests", ["approved"]
    )


def downgrade() -> None:
    op.drop_table("physical_procurement_requests")
    op.drop_table("physical_inventory_events")
    op.drop_table("physical_storage_nodes")
    op.drop_table("physical_items")
