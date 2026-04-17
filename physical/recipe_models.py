"""Recipe models (extension of the Physical Domain).

- ``physical_recipe``: a recipe definition (OCR-ingested or hand-entered)
- ``physical_recipe_ingredient``: recipe line items, optionally linked to a
  PhysicalItem by ``item_id``. When ``item_id`` is NULL the ingredient is
  unmatched (new item candidate) and cooking will skip it.

Cooking a recipe emits ITEM_CONSUMED events via physical.service.record_event
so all existing constraints (insufficient stock, critical threshold, etc.)
apply uniformly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PhysicalRecipe(Base):
    __tablename__ = "physical_recipe"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    yield_servings: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prep_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_ocr: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    instructions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ingredients: Mapped[list["PhysicalRecipeIngredient"]] = relationship(
        "PhysicalRecipeIngredient",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="PhysicalRecipeIngredient.position",
    )

    __table_args__ = (Index("ix_physical_recipe_name", "name"),)


class PhysicalRecipeIngredient(Base):
    __tablename__ = "physical_recipe_ingredient"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    recipe_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("physical_recipe.id", ondelete="CASCADE"),
        nullable=False,
    )
    # item_id is nullable: OCR'd ingredients that don't match a catalog entry
    # are stored anyway, flagged for later resolution.
    item_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("physical_items.id"),
        nullable=True,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="unit")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    recipe: Mapped[PhysicalRecipe] = relationship(
        "PhysicalRecipe", back_populates="ingredients"
    )

    __table_args__ = (
        Index("ix_physical_recipe_ingredient_recipe_id", "recipe_id"),
        Index("ix_physical_recipe_ingredient_item_id", "item_id"),
    )
