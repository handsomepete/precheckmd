"""Recipe HTTP routes.

- POST /physical/recipes/ocr    multipart image upload -> Claude Vision -> structured blob
- POST /physical/recipes        create recipe (from edited OCR result or scratch)
- GET  /physical/recipes        list
- GET  /physical/recipes/{id}   detail
- POST /physical/recipes/{id}/cook  emit ITEM_CONSUMED events for matched ingredients

OCR is a read-only starting point: the user must POST the edited structure
to /physical/recipes to persist anything. Cooking is its own explicit action
with no autosave side effects.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents.recipe_ocr import OcrError, ocr_recipe
from api.auth import require_api_key
from api.deps import get_db
from physical.recipe_service import (
    IngredientInput,
    cook_recipe,
    create_recipe,
    get_recipe,
    list_recipes,
)

router = APIRouter(
    prefix="/physical/recipes",
    tags=["recipes"],
    dependencies=[Depends(require_api_key)],
)


# ---------- schemas ----------


class IngredientIn(BaseModel):
    display_name: str
    quantity: Decimal = Decimal("0")
    unit: str = "unit"
    item_id: str | None = None
    notes: str | None = None


class RecipeIn(BaseModel):
    name: str
    yield_servings: int | None = None
    prep_time_minutes: int | None = None
    source: str | None = None
    image_ref: str | None = None
    raw_ocr: dict[str, Any] | None = None
    instructions: list[str] | None = None
    notes: str | None = None
    ingredients: list[IngredientIn] = Field(default_factory=list)


class CookIn(BaseModel):
    servings: int | None = None


# ---------- serializers ----------


def _serialize_recipe(recipe) -> dict:
    return {
        "id": recipe.id,
        "name": recipe.name,
        "yield_servings": recipe.yield_servings,
        "prep_time_minutes": recipe.prep_time_minutes,
        "source": recipe.source,
        "image_ref": recipe.image_ref,
        "instructions": recipe.instructions or [],
        "notes": recipe.notes,
        "created_at": recipe.created_at.isoformat() if recipe.created_at else None,
        "ingredients": [
            {
                "id": i.id,
                "display_name": i.display_name,
                "quantity": float(i.quantity),
                "unit": i.unit,
                "item_id": i.item_id,
                "position": i.position,
                "notes": i.notes,
            }
            for i in recipe.ingredients
        ],
    }


# ---------- routes ----------


@router.post("/ocr")
async def ocr_route(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty file"
        )
    try:
        result = await ocr_recipe(image_bytes=raw, filename=file.filename)
    except OcrError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail={"error": str(exc)}
        )
    return {
        "title": result.title,
        "yield_servings": result.yield_servings,
        "prep_time_minutes": result.prep_time_minutes,
        "ingredients": result.ingredients,
        "instructions": result.instructions,
        "dry_run": result.dry_run,
    }


@router.post("")
async def create_route(
    payload: RecipeIn, db: AsyncSession = Depends(get_db)
) -> dict:
    recipe = await create_recipe(
        db,
        name=payload.name,
        yield_servings=payload.yield_servings,
        prep_time_minutes=payload.prep_time_minutes,
        source=payload.source,
        image_ref=payload.image_ref,
        raw_ocr=payload.raw_ocr,
        instructions=payload.instructions,
        notes=payload.notes,
        ingredients=[
            IngredientInput(
                display_name=i.display_name,
                quantity=Decimal(str(i.quantity)),
                unit=i.unit,
                item_id=i.item_id,
                notes=i.notes,
            )
            for i in payload.ingredients
        ],
    )
    await db.commit()
    fresh = await get_recipe(db, recipe.id)
    return _serialize_recipe(fresh)


@router.get("")
async def list_route(db: AsyncSession = Depends(get_db)) -> list[dict]:
    recipes = await list_recipes(db)
    return [_serialize_recipe(r) for r in recipes]


@router.get("/{recipe_id}")
async def detail_route(
    recipe_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    recipe = await get_recipe(db, recipe_id)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="recipe not found"
        )
    return _serialize_recipe(recipe)


@router.post("/{recipe_id}/cook")
async def cook_route(
    recipe_id: str,
    payload: CookIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    recipe = await get_recipe(db, recipe_id)
    if recipe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="recipe not found"
        )
    outcome = await cook_recipe(db, recipe_id=recipe_id, servings=payload.servings)
    await db.commit()
    return {
        "recipe_id": outcome.recipe_id,
        "ok": outcome.ok,
        "results": [
            {
                "ingredient_id": r.ingredient_id,
                "item_id": r.item_id,
                "display_name": r.display_name,
                "storage_node_id": r.storage_node_id,
                "quantity": float(r.quantity),
                "status": r.status,
                "error": r.error,
            }
            for r in outcome.results
        ],
    }
