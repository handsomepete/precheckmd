"""Claude Vision OCR for recipe cards.

Takes raw image bytes, asks Claude to transcribe a HelloFresh-style recipe
card into a structured JSON blob. The user is expected to edit the output
before it's persisted — OCR is a starting point, not truth.

Returns a dry-run stub when ``ANTHROPIC_API_KEY`` is unset so the UI flow
is still exercisable locally without burning tokens.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx

from api.config import settings

SYSTEM_PROMPT = """You are the recipe-OCR layer of HomeOS. Given a photo of a
HelloFresh (or similar meal-kit) recipe card, return a single JSON object
describing the recipe. Do not include prose or markdown fences.

Schema:
{
  "title": string,
  "yield_servings": integer or null,
  "prep_time_minutes": integer or null,
  "ingredients": [
    {"quantity": number or null, "unit": string, "name": string, "notes": string or null}
  ],
  "instructions": [string]  // ordered steps
}

Rules:
- Preserve the exact ingredient names as they appear on the card.
- Split compound entries (e.g. "salt & pepper") into separate ingredients.
- Use null for quantity when the card says "to taste", "as needed", etc.
- Units should be lowercase, short form: "oz", "g", "ml", "tsp", "tbsp",
  "cup", "ea", "clove", "pinch". If unclear, use "ea".
- If any field is unreadable, use null (do not guess).
"""


@dataclass
class RecipeOcrResult:
    title: str
    yield_servings: int | None
    prep_time_minutes: int | None
    ingredients: list[dict]
    instructions: list[str]
    raw_response: str
    dry_run: bool


class OcrError(Exception):
    pass


def _guess_media_type(filename: str | None) -> str:
    if not filename:
        return "image/jpeg"
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _dry_run_stub() -> RecipeOcrResult:
    return RecipeOcrResult(
        title="Tuscan Pork Sausage Penne (stub)",
        yield_servings=2,
        prep_time_minutes=30,
        ingredients=[
            {"quantity": 6, "unit": "oz", "name": "pork sausage", "notes": None},
            {"quantity": 1, "unit": "ea", "name": "zucchini", "notes": None},
            {"quantity": 4, "unit": "oz", "name": "penne", "notes": None},
            {"quantity": 1, "unit": "tbsp", "name": "tomato paste", "notes": None},
            {"quantity": 0.25, "unit": "cup", "name": "parmesan cheese", "notes": None},
        ],
        instructions=[
            "Boil a pot of salted water. Cook penne to al dente.",
            "Brown the sausage, add zucchini, then tomato paste.",
            "Toss drained penne with sauce. Finish with parmesan.",
        ],
        raw_response="<dry-run: ANTHROPIC_API_KEY not set>",
        dry_run=True,
    )


async def ocr_recipe(
    *,
    image_bytes: bytes,
    filename: str | None = None,
    model: str = "claude-opus-4-7",
    max_tokens: int = 2048,
    client: httpx.AsyncClient | None = None,
) -> RecipeOcrResult:
    """OCR a recipe card image. Returns a structured RecipeOcrResult."""
    api_key = settings.anthropic_api_key
    if not api_key:
        return _dry_run_stub()

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _guess_media_type(filename),
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Transcribe this recipe card.",
                    },
                ],
            }
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    if client is not None:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages", headers=headers, json=payload
        )
    else:
        async with httpx.AsyncClient(timeout=60.0) as c:
            resp = await c.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=payload
            )
    if resp.status_code >= 400:
        raise OcrError(f"Claude API HTTP {resp.status_code}: {resp.text}")
    body = resp.json()
    text = _extract_text(body)
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        raise OcrError(f"OCR response was not a JSON object: {text[:500]}")

    return RecipeOcrResult(
        title=str(parsed.get("title") or "Untitled"),
        yield_servings=_as_int(parsed.get("yield_servings")),
        prep_time_minutes=_as_int(parsed.get("prep_time_minutes")),
        ingredients=list(parsed.get("ingredients") or []),
        instructions=[str(s) for s in (parsed.get("instructions") or [])],
        raw_response=text,
        dry_run=False,
    )


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_text(body: dict) -> str:
    parts = body.get("content", [])
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            chunks.append(str(part.get("text", "")))
    return "".join(chunks).strip()


def _extract_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[-1]
        if text.lstrip().lower().startswith("json"):
            text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
