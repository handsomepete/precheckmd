"""Operational Domain tables: resources, tasks, events.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-17 00:00:02.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operational_resources",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(64), nullable=False, server_default="person"),
        sa.Column(
            "concurrent_capacity", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "operational_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "duration_minutes", sa.Integer(), nullable=False, server_default="30"
        ),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "required_resource_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_operational_tasks_deadline", "operational_tasks", ["deadline"]
    )

    op.create_table(
        "operational_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("operational_tasks.id"),
            nullable=True,
        ),
        sa.Column(
            "resource_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("operational_resources.id"),
            nullable=True,
        ),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
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
        "ix_operational_events_event_type", "operational_events", ["event_type"]
    )
    op.create_index(
        "ix_operational_events_task_id", "operational_events", ["task_id"]
    )
    op.create_index(
        "ix_operational_events_scheduled_start",
        "operational_events",
        ["scheduled_start"],
    )


def downgrade() -> None:
    op.drop_table("operational_events")
    op.drop_table("operational_tasks")
    op.drop_table("operational_resources")
