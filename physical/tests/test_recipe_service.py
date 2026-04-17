"""Tests for recipe service: create, match, cook (FEFO consumption via mocks).

These tests are database-free. We stub out the async service calls that touch
the DB (build_projection, load_items, record_event) so the logic under test
is purely in recipe_service.py.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from physical.events import PhysicalEventType
from physical.projection import InventoryProjection, Lot
from physical.recipe_models import PhysicalRecipe, PhysicalRecipeIngredient
from physical.recipe_service import (
    CookOutcome,
    IngredientInput,
    _pick_storage_node,
    match_item_by_name,
)


def _run(coro):
    return asyncio.run(coro)


# ---------- match_item_by_name ----------


def _items(*names):
    out = {}
    for i, name in enumerate(names):
        item = MagicMock()
        item.id = f"item-{i}"
        item.name = name
        out[item.id] = item
    return out


def test_exact_match():
    items = _items("Penne Pasta", "Zucchini", "Pork Sausage")
    assert match_item_by_name("penne pasta", items) == "item-0"


def test_substring_match():
    items = _items("Organic Whole Milk")
    assert match_item_by_name("milk", items) == "item-0"


def test_no_match_returns_none():
    items = _items("Pasta", "Cheese")
    assert match_item_by_name("dragon fruit", items) is None


def test_empty_name_returns_none():
    items = _items("Pasta")
    assert match_item_by_name("", items) is None


# ---------- _pick_storage_node ----------


def _projection_with(*lots) -> InventoryProjection:
    proj = InventoryProjection()
    for item_id, node_id, qty in lots:
        key = (item_id, node_id, None)
        proj.lots[key] = Lot(item_id=item_id, storage_node_id=node_id,
                             expires_at=None, quantity=Decimal(str(qty)))
    return proj


def test_pick_node_prefers_sufficient_stock():
    proj = _projection_with(
        ("item-1", "pantry", "2"),
        ("item-1", "fridge", "10"),
    )
    node = _pick_storage_node(proj, "item-1", Decimal("5"))
    assert node == "fridge"


def test_pick_node_returns_none_when_item_absent():
    proj = _projection_with(("item-2", "pantry", "5"))
    assert _pick_storage_node(proj, "item-1", Decimal("1")) is None


def test_pick_node_ignores_empty_lots():
    proj = _projection_with(("item-1", "pantry", "0"), ("item-1", "fridge", "3"))
    node = _pick_storage_node(proj, "item-1", Decimal("1"))
    assert node == "fridge"


# ---------- cook_recipe integration ----------


def _make_recipe(ingredients):
    recipe = MagicMock(spec=PhysicalRecipe)
    recipe.id = "recipe-001"
    recipe.yield_servings = 2
    recipe.ingredients = ingredients
    return recipe


def _make_ing(i, item_id=None, qty="1", unit="ea"):
    ing = MagicMock(spec=PhysicalRecipeIngredient)
    ing.id = f"ing-{i}"
    ing.item_id = item_id
    ing.display_name = f"ingredient {i}"
    ing.quantity = Decimal(qty)
    ing.unit = unit
    return ing


@pytest.mark.asyncio
async def test_cook_all_consumed():
    from physical.recipe_service import cook_recipe

    recipe = _make_recipe([_make_ing(1, "item-1", "2")])
    proj = _projection_with(("item-1", "pantry", "10"))

    with (
        patch("physical.recipe_service.get_recipe", new=AsyncMock(return_value=recipe)),
        patch("physical.recipe_service.build_projection", new=AsyncMock(return_value=proj)),
        patch("physical.recipe_service.record_event", new=AsyncMock()) as mock_record,
    ):
        outcome = await cook_recipe(MagicMock(), recipe_id="recipe-001")

    assert outcome.ok is True
    assert outcome.results[0].status == "consumed"
    assert outcome.results[0].storage_node_id == "pantry"
    mock_record.assert_awaited_once()
    call_payload = mock_record.call_args[0][1]
    assert call_payload.event_type == PhysicalEventType.ITEM_CONSUMED
    assert call_payload.quantity == Decimal("2")


@pytest.mark.asyncio
async def test_cook_unmatched_ingredient_skipped():
    from physical.recipe_service import cook_recipe

    matched = _make_ing(1, "item-1", "2")
    unmatched = _make_ing(2, None, "1")
    recipe = _make_recipe([matched, unmatched])
    proj = _projection_with(("item-1", "pantry", "10"))

    with (
        patch("physical.recipe_service.get_recipe", new=AsyncMock(return_value=recipe)),
        patch("physical.recipe_service.build_projection", new=AsyncMock(return_value=proj)),
        patch("physical.recipe_service.record_event", new=AsyncMock()),
    ):
        outcome = await cook_recipe(MagicMock(), recipe_id="recipe-001")

    assert outcome.ok is True
    statuses = {r.ingredient_id: r.status for r in outcome.results}
    assert statuses["ing-1"] == "consumed"
    assert statuses["ing-2"] == "unmatched"


@pytest.mark.asyncio
async def test_cook_stops_on_insufficient_stock():
    from physical.recipe_service import cook_recipe, ConstraintViolation
    from physical.constraints import ConstraintReport, Violation, ViolationCode

    ing1 = _make_ing(1, "item-1", "2")
    ing2 = _make_ing(2, "item-2", "3")
    ing3 = _make_ing(3, "item-3", "1")
    recipe = _make_recipe([ing1, ing2, ing3])

    proj = _projection_with(
        ("item-1", "pantry", "10"),
        ("item-2", "pantry", "1"),  # insufficient for 3 units
        ("item-3", "pantry", "5"),
    )

    def _record_side_effect(session, payload):
        if payload.item_id == "item-2":
            report = ConstraintReport(violations=[
                Violation(code=ViolationCode.NEGATIVE_INVENTORY,
                          message="not enough stock")
            ])
            raise ConstraintViolation(report)

    with (
        patch("physical.recipe_service.get_recipe", new=AsyncMock(return_value=recipe)),
        patch("physical.recipe_service.build_projection", new=AsyncMock(return_value=proj)),
        patch("physical.recipe_service.record_event",
              new=AsyncMock(side_effect=_record_side_effect)),
    ):
        outcome = await cook_recipe(MagicMock(), recipe_id="recipe-001")

    assert outcome.ok is False
    statuses = {r.ingredient_id: r.status for r in outcome.results}
    assert statuses["ing-1"] == "consumed"
    assert statuses["ing-2"] == "insufficient"
    assert statuses["ing-3"] == "skipped"


@pytest.mark.asyncio
async def test_cook_scales_by_servings():
    from physical.recipe_service import cook_recipe

    ing = _make_ing(1, "item-1", "2")  # 2 units per 2 servings
    recipe = _make_recipe([ing])
    proj = _projection_with(("item-1", "pantry", "10"))

    recorded_qty = []

    async def _capture(session, payload):
        recorded_qty.append(payload.quantity)

    with (
        patch("physical.recipe_service.get_recipe", new=AsyncMock(return_value=recipe)),
        patch("physical.recipe_service.build_projection", new=AsyncMock(return_value=proj)),
        patch("physical.recipe_service.record_event", new=AsyncMock(side_effect=_capture)),
    ):
        await cook_recipe(MagicMock(), recipe_id="recipe-001", servings=4)

    # 2 * (4/2) = 4
    assert recorded_qty[0] == Decimal("4")
