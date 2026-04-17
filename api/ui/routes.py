"""Server-rendered HTMX UI for HomeOS.

All pages are rendered server-side from Jinja templates. HTMX handles
partial updates (form posts, card-ingest flow, plan/execute). UI routes
call domain services directly — no HTTP loopback.

Auth: cookie-based API-key session set at /ui/login.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.planner import PlannerError, plan as run_planner
from agents.recipe_ocr import OcrError, ocr_recipe
from api.config import settings
from api.deps import get_db
from api.ui.auth import (
    clear_session_cookie,
    is_authenticated,
    require_ui_session,
    set_session_cookie,
)
from api.ui.templates import templates
from financial.projection import available_budget as financial_available_budget
from financial.service import current_state as financial_state
from openclaw.audit import ActionAudit
from openclaw.executor import execute_plan
from operational.service import current_state as operational_state
from physical.recipe_service import (
    IngredientInput,
    cook_recipe,
    create_recipe,
    get_recipe,
    list_recipes,
)
from physical.service import current_state as physical_state
from validator.validator import validate_plan

router = APIRouter(prefix="/ui", tags=["ui"])


# ---------- login ----------


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/ui"):
    return templates.TemplateResponse(
        "login.html", {"request": request, "next": next, "error": None}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    api_key: str = Form(...),
    next: str = Form("/ui"),
):
    if api_key != settings.api_key:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "next": next, "error": "Invalid API key."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse(url=next or "/ui", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, api_key)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/ui/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response


# ---------- helpers ----------


async def _state_snapshot(db: AsyncSession) -> dict[str, Any]:
    ph_proj, ph_items, ph_nodes, ph_report = await physical_state(db)
    fi_proj, fi_accounts, fi_obligations, fi_report = await financial_state(db)
    op_proj, op_resources, op_tasks, op_report = await operational_state(db)
    budget = await financial_available_budget(db)

    lots = sorted(
        ph_proj.non_empty_lots(),
        key=lambda l: (l.expires_at is None, l.expires_at or datetime.max),
    )
    now = datetime.now(timezone.utc)
    near_expiry = [
        l for l in lots if l.expires_at and (l.expires_at - now).days <= 3
    ]

    low_stock = []
    for item in ph_items.values():
        q = ph_proj.quantity(item.id)
        thresh = Decimal(str(item.reorder_threshold or 0))
        if thresh > 0 and q < thresh:
            low_stock.append({"item": item, "quantity": q, "threshold": thresh})

    violations = (
        [{"domain": "physical", "code": v.code.value, "message": v.message} for v in ph_report.violations]
        + [{"domain": "financial", "code": v.code.value, "message": v.message} for v in fi_report.violations]
        + [{"domain": "operational", "code": v.code.value, "message": v.message} for v in op_report.violations]
    )

    return {
        "physical": {
            "projection": ph_proj,
            "items": ph_items,
            "nodes": ph_nodes,
            "lots": lots,
            "near_expiry": near_expiry,
            "low_stock": low_stock,
        },
        "financial": {
            "projection": fi_proj,
            "accounts": fi_accounts,
            "obligations": fi_obligations,
            "available_budget": budget,
        },
        "operational": {
            "projection": op_proj,
            "resources": op_resources,
            "tasks": op_tasks,
        },
        "violations": violations,
    }


# ---------- pages ----------


@router.get("", response_class=HTMLResponse)
async def home_redirect():
    return RedirectResponse(url="/ui/home", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/home", response_class=HTMLResponse)
async def home(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    snap = await _state_snapshot(db)
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "snap": snap, "page": "home"},
    )


@router.get("/inventory", response_class=HTMLResponse)
async def inventory(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    snap = await _state_snapshot(db)
    return templates.TemplateResponse(
        "inventory.html",
        {"request": request, "snap": snap, "page": "inventory"},
    )


@router.get("/recipes", response_class=HTMLResponse)
async def recipes_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    recipes = await list_recipes(db)
    return templates.TemplateResponse(
        "recipes_list.html",
        {"request": request, "recipes": recipes, "page": "recipes"},
    )


@router.get("/recipes/new", response_class=HTMLResponse)
async def recipes_new(
    request: Request,
    _=Depends(require_ui_session),
):
    return templates.TemplateResponse(
        "recipes_new.html",
        {"request": request, "page": "recipes"},
    )


@router.post("/recipes/ocr", response_class=HTMLResponse)
async def recipes_ocr(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    raw = await file.read()
    if not raw:
        return HTMLResponse(
            "<div class='text-red-600 p-3'>empty file</div>",
            status_code=400,
        )
    try:
        result = await ocr_recipe(image_bytes=raw, filename=file.filename)
    except OcrError as exc:
        return HTMLResponse(
            f"<div class='text-red-600 p-3'>OCR failed: {exc}</div>",
            status_code=502,
        )

    # Match ingredients to catalog items for preview.
    from physical.projection import load_items
    from physical.recipe_service import match_item_by_name

    items = await load_items(db)
    enriched = []
    for raw_ing in result.ingredients:
        name = str(raw_ing.get("name") or "")
        matched_id = match_item_by_name(name, items)
        enriched.append(
            {
                "name": name,
                "quantity": raw_ing.get("quantity"),
                "unit": raw_ing.get("unit") or "ea",
                "notes": raw_ing.get("notes"),
                "item_id": matched_id,
                "item_name": items[matched_id].name if matched_id else None,
            }
        )

    return templates.TemplateResponse(
        "_recipe_edit_form.html",
        {
            "request": request,
            "title": result.title,
            "yield_servings": result.yield_servings,
            "prep_time_minutes": result.prep_time_minutes,
            "ingredients": enriched,
            "instructions": result.instructions,
            "items": items,
            "dry_run": result.dry_run,
        },
    )


@router.post("/recipes")
async def recipes_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    form = await request.form()
    name = str(form.get("name") or "").strip() or "Untitled"
    yield_servings = _to_int(form.get("yield_servings"))
    prep_time_minutes = _to_int(form.get("prep_time_minutes"))
    instructions_raw = str(form.get("instructions") or "")
    instructions = [line for line in instructions_raw.splitlines() if line.strip()]

    ing_names = form.getlist("ing_name")
    ing_qtys = form.getlist("ing_qty")
    ing_units = form.getlist("ing_unit")
    ing_item_ids = form.getlist("ing_item_id")

    ingredients: list[IngredientInput] = []
    for i, nm in enumerate(ing_names):
        nm = str(nm).strip()
        if not nm:
            continue
        try:
            qty = Decimal(str(ing_qtys[i])) if i < len(ing_qtys) and ing_qtys[i] else Decimal("0")
        except Exception:
            qty = Decimal("0")
        unit = str(ing_units[i]) if i < len(ing_units) else "ea"
        item_id = str(ing_item_ids[i]) if i < len(ing_item_ids) else ""
        ingredients.append(
            IngredientInput(
                display_name=nm,
                quantity=qty,
                unit=unit or "ea",
                item_id=item_id or None,
            )
        )

    recipe = await create_recipe(
        db,
        name=name,
        yield_servings=yield_servings,
        prep_time_minutes=prep_time_minutes,
        source="hellofresh_ocr",
        instructions=instructions,
        ingredients=ingredients,
    )
    await db.commit()
    return RedirectResponse(
        url=f"/ui/recipes/{recipe.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/recipes/{recipe_id}", response_class=HTMLResponse)
async def recipes_detail(
    recipe_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    recipe = await get_recipe(db, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found")
    snap = await _state_snapshot(db)
    return templates.TemplateResponse(
        "recipes_detail.html",
        {
            "request": request,
            "recipe": recipe,
            "items": snap["physical"]["items"],
            "page": "recipes",
        },
    )


@router.post("/recipes/{recipe_id}/cook", response_class=HTMLResponse)
async def recipes_cook(
    recipe_id: str,
    request: Request,
    servings: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    recipe = await get_recipe(db, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="recipe not found")
    outcome = await cook_recipe(db, recipe_id=recipe_id, servings=servings)
    await db.commit()
    return templates.TemplateResponse(
        "_cook_result.html",
        {"request": request, "outcome": outcome},
    )


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(
    request: Request,
    _=Depends(require_ui_session),
):
    return templates.TemplateResponse(
        "plan.html",
        {"request": request, "page": "plan"},
    )


@router.post("/plan/propose", response_class=HTMLResponse)
async def plan_propose(
    request: Request,
    goal: str = Form(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    snap = await _state_snapshot(db)
    snapshot_json = {
        "physical": {
            "lots": [
                {
                    "item_id": l.item_id,
                    "storage_node_id": l.storage_node_id,
                    "quantity": float(l.quantity),
                    "expires_at": l.expires_at.isoformat() if l.expires_at else None,
                }
                for l in snap["physical"]["lots"]
            ]
        },
        "financial": {
            "available_budget": float(snap["financial"]["available_budget"]),
        },
    }
    try:
        result = await run_planner(goal=goal, state_snapshot=snapshot_json)
    except PlannerError as exc:
        return HTMLResponse(
            f"<div class='text-red-600 p-3'>planner error: {exc}</div>",
            status_code=502,
        )

    validation = validate_plan(result.plan, approvals=set())
    return templates.TemplateResponse(
        "_plan_result.html",
        {
            "request": request,
            "plan": result.plan,
            "planner": {"model": result.model, "dry_run": result.dry_run},
            "validation": validation,
        },
    )


@router.post("/plan/execute", response_class=HTMLResponse)
async def plan_execute(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    form = await request.form()
    import json as _json

    try:
        plan_obj = _json.loads(str(form.get("plan_json") or "{}"))
    except Exception as exc:
        return HTMLResponse(
            f"<div class='text-red-600 p-3'>bad plan JSON: {exc}</div>",
            status_code=400,
        )

    approvals = set(form.getlist("approvals"))
    validation = validate_plan(plan_obj, approvals=approvals)
    outcome = await execute_plan(db, plan_obj, validation)
    await db.commit()
    return templates.TemplateResponse(
        "_execute_result.html",
        {
            "request": request,
            "validation": validation,
            "outcome": outcome,
        },
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_ui_session),
):
    stmt = select(ActionAudit).order_by(desc(ActionAudit.started_at)).limit(100)
    result = await db.execute(stmt)
    rows = list(result.scalars())
    return templates.TemplateResponse(
        "audit.html",
        {"request": request, "rows": rows, "page": "audit"},
    )


# ---------- small helpers ----------


def _to_int(value) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
