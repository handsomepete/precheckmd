"""Recipe service.

Create, read, and cook recipes. Cooking emits ITEM_CONSUMED events through
``physical.service.record_event`` so every existing Physical Domain
constraint (insufficient stock, critical threshold, unknown item, ...)
applies uniformly.

Matching OCR'd ingredient names to catalog items is a best-effort string
match (case-insensitive exact, then substring). Unmatched ingredients are
stored with ``item_id=NULL`` and skipped during cooking (surfaced in the UI
as "unmatched").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from physical.events import PhysicalEventType
from physical.models import PhysicalInventoryEvent, PhysicalItem
from physical.projection import build_projection, load_items
from physical.recipe_models import PhysicalRecipe, PhysicalRecipeIngredient
from physical.service import ConstraintViolation, RecordEventInput, record_event


@dataclass
class IngredientInput:
    display_name: str
    quantity: Decimal
    unit: str
    item_id: str | None = None
    notes: str | None = None


@dataclass
class CookIngredientResult:
    ingredient_id: str
    item_id: str | None
    display_name: str
    storage_node_id: str | None
    quantity: Decimal
    status: str  # "consumed" | "unmatched" | "insufficient"
    error: str | None = None


@dataclass
class CookOutcome:
    recipe_id: str
    ok: bool
    results: list[CookIngredientResult]


def match_item_by_name(name: str, items: dict[str, PhysicalItem]) -> str | None:
    """Best-effort name -> item_id resolution (case-insensitive)."""
    if not name:
        return None
    needle = name.strip().lower()
    # exact match first
    for item_id, item in items.items():
        if item.name.lower() == needle:
            return item_id
    # substring either direction
    for item_id, item in items.items():
        lname = item.name.lower()
        if lname in needle or needle in lname:
            return item_id
    return None


async def create_recipe(
    session: AsyncSession,
    *,
    name: str,
    ingredients: list[IngredientInput],
    yield_servings: int | None = None,
    prep_time_minutes: int | None = None,
    source: str | None = None,
    image_ref: str | None = None,
    raw_ocr: dict[str, Any] | None = None,
    instructions: list[Any] | None = None,
    notes: str | None = None,
) -> PhysicalRecipe:
    recipe = PhysicalRecipe(
        name=name,
        yield_servings=yield_servings,
        prep_time_minutes=prep_time_minutes,
        source=source,
        image_ref=image_ref,
        raw_ocr=raw_ocr,
        instructions=instructions,
        notes=notes,
    )
    session.add(recipe)
    await session.flush()

    items = await load_items(session)
    for i, ing in enumerate(ingredients):
        item_id = ing.item_id or match_item_by_name(ing.display_name, items)
        session.add(
            PhysicalRecipeIngredient(
                recipe_id=recipe.id,
                item_id=item_id,
                display_name=ing.display_name,
                quantity=ing.quantity,
                unit=ing.unit,
                notes=ing.notes,
                position=i,
            )
        )
    await session.flush()
    return recipe


async def list_recipes(session: AsyncSession) -> list[PhysicalRecipe]:
    result = await session.execute(
        select(PhysicalRecipe)
        .options(selectinload(PhysicalRecipe.ingredients))
        .order_by(PhysicalRecipe.created_at.desc())
    )
    return list(result.scalars())


async def get_recipe(session: AsyncSession, recipe_id: str) -> PhysicalRecipe | None:
    result = await session.execute(
        select(PhysicalRecipe)
        .options(selectinload(PhysicalRecipe.ingredients))
        .where(PhysicalRecipe.id == recipe_id)
    )
    return result.scalar_one_or_none()


def _pick_storage_node(
    projection, item_id: str, required_qty: Decimal
) -> str | None:
    """Pick the storage node with the most of ``item_id``.

    We prefer a node that can satisfy the full quantity from a single node;
    otherwise the node with the largest quantity (partial consumption is
    still allowed; the caller will record whatever the projection permits).
    """
    totals: dict[str, Decimal] = {}
    for (iid, node_id, _exp), lot in projection.lots.items():
        if iid != item_id or lot.quantity <= 0:
            continue
        totals[node_id] = totals.get(node_id, Decimal("0")) + lot.quantity
    if not totals:
        return None
    # Sort: nodes with enough stock first, then by total desc.
    ranked = sorted(
        totals.items(),
        key=lambda kv: (kv[1] < required_qty, -float(kv[1])),
    )
    return ranked[0][0]


async def cook_recipe(
    session: AsyncSession,
    *,
    recipe_id: str,
    servings: int | None = None,
) -> CookOutcome:
    """Emit ITEM_CONSUMED events for each matched ingredient.

    The quantity is scaled by ``servings / recipe.yield_servings`` when both
    are provided. Unmatched ingredients (item_id is NULL) are skipped with
    status ``unmatched``. An insufficient-stock violation yields
    ``insufficient`` for that ingredient; cooking halts on the first
    failure so partial consumption does not occur.
    """
    recipe = await get_recipe(session, recipe_id)
    if recipe is None:
        raise ValueError(f"recipe not found: {recipe_id}")

    scale = Decimal("1")
    if servings and recipe.yield_servings:
        scale = Decimal(str(servings)) / Decimal(str(recipe.yield_servings))

    projection = await build_projection(session)
    results: list[CookIngredientResult] = []
    halted = False

    for ing in recipe.ingredients:
        qty = Decimal(str(ing.quantity)) * scale

        if ing.item_id is None:
            results.append(
                CookIngredientResult(
                    ingredient_id=ing.id,
                    item_id=None,
                    display_name=ing.display_name,
                    storage_node_id=None,
                    quantity=qty,
                    status="unmatched",
                )
            )
            continue

        if halted:
            results.append(
                CookIngredientResult(
                    ingredient_id=ing.id,
                    item_id=ing.item_id,
                    display_name=ing.display_name,
                    storage_node_id=None,
                    quantity=qty,
                    status="skipped",
                )
            )
            continue

        node_id = _pick_storage_node(projection, ing.item_id, qty)
        if node_id is None:
            results.append(
                CookIngredientResult(
                    ingredient_id=ing.id,
                    item_id=ing.item_id,
                    display_name=ing.display_name,
                    storage_node_id=None,
                    quantity=qty,
                    status="insufficient",
                    error="no storage node has this item",
                )
            )
            halted = True
            continue

        try:
            await record_event(
                session,
                RecordEventInput(
                    event_type=PhysicalEventType.ITEM_CONSUMED,
                    item_id=ing.item_id,
                    storage_node_id=node_id,
                    quantity=qty,
                    metadata={"recipe_id": recipe.id, "ingredient_id": ing.id},
                ),
            )
        except ConstraintViolation as exc:
            results.append(
                CookIngredientResult(
                    ingredient_id=ing.id,
                    item_id=ing.item_id,
                    display_name=ing.display_name,
                    storage_node_id=node_id,
                    quantity=qty,
                    status="insufficient",
                    error="; ".join(v.message for v in exc.report.violations),
                )
            )
            halted = True
            continue

        results.append(
            CookIngredientResult(
                ingredient_id=ing.id,
                item_id=ing.item_id,
                display_name=ing.display_name,
                storage_node_id=node_id,
                quantity=qty,
                status="consumed",
            )
        )
        projection = await build_projection(session)

    ok = not halted and all(r.status in ("consumed", "unmatched") for r in results)
    return CookOutcome(recipe_id=recipe.id, ok=ok, results=results)
