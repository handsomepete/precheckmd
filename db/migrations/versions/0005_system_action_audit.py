"""OpenCLAW system_action_audit table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-17 00:00:03.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_action_audit",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("action_id", sa.String(128), nullable=False),
        sa.Column("target", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column(
            "parameters", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "risk_level", sa.String(16), nullable=False, server_default="low"
        ),
        sa.Column(
            "outcome", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dry_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_system_action_audit_plan_id", "system_action_audit", ["plan_id"]
    )
    op.create_index(
        "ix_system_action_audit_outcome", "system_action_audit", ["outcome"]
    )


def downgrade() -> None:
    op.drop_table("system_action_audit")
