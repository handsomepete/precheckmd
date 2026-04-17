"""Jinja2 environment and template rendering helpers for the UI."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _humanize_quantity(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _humanize_datetime(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M")


def _humanize_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        dt = value
    return dt.strftime("%Y-%m-%d")


templates.env.filters["qty"] = _humanize_quantity
templates.env.filters["dt"] = _humanize_datetime
templates.env.filters["d"] = _humanize_date
