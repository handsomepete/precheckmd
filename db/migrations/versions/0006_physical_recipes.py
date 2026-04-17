"""Physical recipes (recipe + ingredient tables).

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-17 00:00:04.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "physical_recipe",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("yield_servings", sa.Integer(), nullable=True),
        sa.Column("prep_time_minutes", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("image_ref", sa.Text(), nullable=True),
        sa.Column("raw_ocr", postgresql.JSONB(), nullable=True),
        sa.Column("instructions", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_physical_recipe_name", "physical_recipe", ["name"])

    op.create_table(
        "physical_recipe_ingredient",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "recipe_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("physical_recipe.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("physical_items.id"),
            nullable=True,
        ),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unit", sa.String(32), nullable=False, server_default="unit"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_physical_recipe_ingredient_recipe_id",
        "physical_recipe_ingredient",
        ["recipe_id"],
    )
    op.create_index(
        "ix_physical_recipe_ingredient_item_id",
        "physical_recipe_ingredient",
        ["item_id"],
    )


def downgrade() -> None:
    op.drop_table("physical_recipe_ingredient")
    op.drop_table("physical_recipe")
