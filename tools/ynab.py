"""YNAB API client — thin wrapper around the YNAB v1 REST API.

All dollar amounts are converted to/from YNAB milliunits here so that
callers (MCP tool handlers) always deal in plain dollars.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

_BASE = "https://api.ynab.com/v1"


# ---------- config ----------


def _token() -> str:
    t = os.environ.get("YNAB_API_TOKEN", "")
    if not t:
        raise RuntimeError("YNAB_API_TOKEN environment variable is not set")
    return t


def _budget_id() -> str:
    return os.environ.get("YNAB_BUDGET_ID", "last-used")


# ---------- unit conversion ----------


def _to_milliunits(dollars: float) -> int:
    return round(dollars * 1000)


def _from_milliunits(milliunits: int | None) -> float:
    return (milliunits or 0) / 1000


# ---------- HTTP helpers ----------


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


def _get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(f"{_BASE}{path}", headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    r = httpx.post(f"{_BASE}{path}", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: dict) -> dict:
    r = httpx.patch(f"{_BASE}{path}", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _delete(path: str) -> dict:
    r = httpx.delete(f"{_BASE}{path}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


# ---------- response formatters ----------


def _fmt_txn(t: dict) -> dict:
    return {
        "id": t["id"],
        "date": t["date"],
        "amount": _from_milliunits(t.get("amount")),
        "memo": t.get("memo"),
        "cleared": t.get("cleared"),
        "approved": t.get("approved"),
        "payee_name": t.get("payee_name"),
        "account_id": t.get("account_id"),
        "account_name": t.get("account_name"),
        "category_id": t.get("category_id"),
        "category_name": t.get("category_name"),
    }


def _fmt_month_summary(m: dict) -> dict:
    return {
        "month": m["month"],
        "income": _from_milliunits(m.get("income")),
        "budgeted": _from_milliunits(m.get("budgeted")),
        "activity": _from_milliunits(m.get("activity")),
        "to_be_budgeted": _from_milliunits(m.get("to_be_budgeted")),
    }


# ---------- accounts ----------


def get_accounts() -> list[dict]:
    data = _get(f"/budgets/{_budget_id()}/accounts")
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "type": a["type"],
            "balance": _from_milliunits(a.get("balance")),
            "cleared_balance": _from_milliunits(a.get("cleared_balance")),
            "uncleared_balance": _from_milliunits(a.get("uncleared_balance")),
            "on_budget": a.get("on_budget"),
            "closed": a.get("closed"),
        }
        for a in data["data"]["accounts"]
        if not a.get("deleted")
    ]


# ---------- transactions ----------


def get_account_transactions(
    account_id: str,
    since: str | None = None,
    before: str | None = None,
) -> list[dict]:
    params: dict[str, str] = {}
    if since:
        params["since_date"] = since
    data = _get(f"/budgets/{_budget_id()}/accounts/{account_id}/transactions", params=params)
    txns = data["data"]["transactions"]
    if before:
        txns = [t for t in txns if t["date"] <= before]
    return [_fmt_txn(t) for t in txns]


def get_transactions(
    since: str | None = None,
    before: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    payee_name: str | None = None,
) -> list[dict]:
    params: dict[str, str] = {}
    if since:
        params["since_date"] = since
    data = _get(f"/budgets/{_budget_id()}/transactions", params=params)
    txns = data["data"]["transactions"]
    if before:
        txns = [t for t in txns if t["date"] <= before]
    if account_id:
        txns = [t for t in txns if t.get("account_id") == account_id]
    if category_id:
        txns = [t for t in txns if t.get("category_id") == category_id]
    if payee_name:
        needle = payee_name.lower()
        txns = [t for t in txns if needle in (t.get("payee_name") or "").lower()]
    return [_fmt_txn(t) for t in txns]


def get_transaction(transaction_id: str) -> dict:
    data = _get(f"/budgets/{_budget_id()}/transactions/{transaction_id}")
    return _fmt_txn(data["data"]["transaction"])


def create_transaction(
    account_id: str,
    date: str,
    payee_name: str,
    amount: float,
    category_id: str | None = None,
    memo: str | None = None,
) -> dict:
    txn: dict[str, Any] = {
        "account_id": account_id,
        "date": date,
        "amount": _to_milliunits(amount),
        "payee_name": payee_name,
        "cleared": "cleared",
        "approved": True,
    }
    if category_id:
        txn["category_id"] = category_id
    if memo:
        txn["memo"] = memo
    data = _post(f"/budgets/{_budget_id()}/transactions", {"transaction": txn})
    return _fmt_txn(data["data"]["transaction"])


def update_transaction(
    transaction_id: str,
    cleared: str | None = None,
    amount: float | None = None,
    date: str | None = None,
    payee_name: str | None = None,
    memo: str | None = None,
    category_id: str | None = None,
) -> dict:
    fields: dict[str, Any] = {}
    if cleared is not None:
        fields["cleared"] = cleared
    if amount is not None:
        fields["amount"] = _to_milliunits(amount)
    if date is not None:
        fields["date"] = date
    if payee_name is not None:
        fields["payee_name"] = payee_name
    if memo is not None:
        fields["memo"] = memo
    if category_id is not None:
        fields["category_id"] = category_id
    data = _patch(
        f"/budgets/{_budget_id()}/transactions/{transaction_id}",
        {"transaction": fields},
    )
    return _fmt_txn(data["data"]["transaction"])


def update_transactions_bulk(transactions: list[dict]) -> dict:
    """Each item must have 'id'; remaining fields are optional patches.

    Optional per-item fields: cleared, amount (dollars), date, payee_name,
    memo, category_id.
    """
    formatted: list[dict[str, Any]] = []
    for t in transactions:
        item: dict[str, Any] = {"id": t["id"]}
        if "cleared" in t:
            item["cleared"] = t["cleared"]
        if "amount" in t:
            item["amount"] = _to_milliunits(t["amount"])
        if "date" in t:
            item["date"] = t["date"]
        if "payee_name" in t:
            item["payee_name"] = t["payee_name"]
        if "memo" in t:
            item["memo"] = t["memo"]
        if "category_id" in t:
            item["category_id"] = t["category_id"]
        formatted.append(item)
    data = _patch(f"/budgets/{_budget_id()}/transactions", {"transactions": formatted})
    bulk = data["data"]["bulk"]
    return {
        "transaction_ids_updated": bulk.get("transaction_ids", []),
        "transaction_ids_added": bulk.get("transaction_ids_added", []),
    }


def delete_transaction(transaction_id: str) -> dict:
    data = _delete(f"/budgets/{_budget_id()}/transactions/{transaction_id}")
    return {"deleted": True, "id": data["data"]["transaction"]["id"]}


# ---------- scheduled transactions ----------


def create_scheduled_transaction(
    account_id: str,
    date: str,
    frequency: str,
    amount: float,
    payee_name: str | None = None,
    category_id: str | None = None,
    memo: str | None = None,
) -> dict:
    st: dict[str, Any] = {
        "account_id": account_id,
        "date": date,
        "frequency": frequency,
        "amount": _to_milliunits(amount),
    }
    if payee_name:
        st["payee_name"] = payee_name
    if category_id:
        st["category_id"] = category_id
    if memo:
        st["memo"] = memo
    data = _post(f"/budgets/{_budget_id()}/scheduled_transactions", {"scheduled_transaction": st})
    s = data["data"]["scheduled_transaction"]
    return {
        "id": s["id"],
        "date_first": s.get("date_first"),
        "date_next": s.get("date_next"),
        "frequency": s.get("frequency"),
        "amount": _from_milliunits(s.get("amount")),
        "payee_name": s.get("payee_name"),
        "category_id": s.get("category_id"),
        "account_id": s.get("account_id"),
        "memo": s.get("memo"),
    }


def get_scheduled_transactions() -> list[dict]:
    data = _get(f"/budgets/{_budget_id()}/scheduled_transactions")
    return [
        {
            "id": s["id"],
            "date_first": s.get("date_first"),
            "date_next": s.get("date_next"),
            "frequency": s.get("frequency"),
            "amount": _from_milliunits(s.get("amount")),
            "payee_name": s.get("payee_name"),
            "category_id": s.get("category_id"),
            "account_id": s.get("account_id"),
            "memo": s.get("memo"),
        }
        for s in data["data"]["scheduled_transactions"]
        if not s.get("deleted")
    ]


# ---------- categories ----------


def get_categories(month: str | None = None) -> list[dict]:
    if month:
        month_str = (month + "-01") if len(month) == 7 else month
        data = _get(f"/budgets/{_budget_id()}/months/{month_str}")
        cats = data["data"]["month"].get("categories", [])
    else:
        data = _get(f"/budgets/{_budget_id()}/categories")
        cats = []
        for group in data["data"]["category_groups"]:
            if group.get("deleted") or group.get("hidden"):
                continue
            cats.extend(group.get("categories", []))
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "category_group_name": c.get("category_group_name"),
            "budgeted": _from_milliunits(c.get("budgeted")),
            "activity": _from_milliunits(c.get("activity")),
            "balance": _from_milliunits(c.get("balance")),
            "hidden": c.get("hidden", False),
        }
        for c in cats
        if not c.get("deleted")
    ]


def update_category_budget(
    month: str,
    category_id: str,
    budgeted: float,
) -> dict:
    """Set the budgeted amount for a category in a given month.

    month: YYYY-MM or YYYY-MM-DD
    budgeted: dollar amount to budget
    """
    month_str = (month + "-01") if len(month) == 7 else month
    data = _patch(
        f"/budgets/{_budget_id()}/months/{month_str}/categories/{category_id}",
        {"category": {"budgeted": _to_milliunits(budgeted)}},
    )
    c = data["data"]["category"]
    return {
        "id": c["id"],
        "name": c["name"],
        "budgeted": _from_milliunits(c.get("budgeted")),
        "activity": _from_milliunits(c.get("activity")),
        "balance": _from_milliunits(c.get("balance")),
    }


# ---------- payees ----------


def get_payees() -> list[dict]:
    data = _get(f"/budgets/{_budget_id()}/payees")
    return [
        {"id": p["id"], "name": p["name"]}
        for p in data["data"]["payees"]
        if not p.get("deleted")
    ]


# ---------- months ----------


def get_months() -> list[dict]:
    data = _get(f"/budgets/{_budget_id()}/months")
    return [_fmt_month_summary(m) for m in data["data"]["months"]]


def get_month(month: str) -> dict:
    """Get full detail for a single budget month including all category balances.

    month: YYYY-MM or YYYY-MM-DD
    """
    month_str = (month + "-01") if len(month) == 7 else month
    data = _get(f"/budgets/{_budget_id()}/months/{month_str}")
    m = data["data"]["month"]
    return {
        **_fmt_month_summary(m),
        "note": m.get("note"),
        "categories": [
            {
                "id": c["id"],
                "name": c["name"],
                "budgeted": _from_milliunits(c.get("budgeted")),
                "activity": _from_milliunits(c.get("activity")),
                "balance": _from_milliunits(c.get("balance")),
            }
            for c in m.get("categories", [])
            if not c.get("deleted") and not c.get("hidden")
        ],
    }


# ---------- budget / settings ----------


def get_finances() -> dict:
    """Return current-month budget snapshot: income, budgeted, TBB, overspent."""
    today = datetime.now()
    month_str = today.strftime("%Y-%m-01")
    data = _get(f"/budgets/{_budget_id()}/months/{month_str}")
    m = data["data"]["month"]
    overspent = [
        {
            "name": c["name"],
            "budgeted": _from_milliunits(c.get("budgeted")),
            "activity": _from_milliunits(c.get("activity")),
            "balance": _from_milliunits(c.get("balance")),
        }
        for c in m.get("categories", [])
        if (c.get("balance") or 0) < 0 and not c.get("hidden") and not c.get("deleted")
    ]
    return {
        "month": month_str,
        "income": _from_milliunits(m.get("income")),
        "budgeted": _from_milliunits(m.get("budgeted")),
        "activity": _from_milliunits(m.get("activity")),
        "to_be_budgeted": _from_milliunits(m.get("to_be_budgeted")),
        "overspent_categories": overspent,
    }


def get_budget_settings() -> dict:
    data = _get(f"/budgets/{_budget_id()}/settings")
    s = data["data"]["settings"]
    return {
        "date_format": (s.get("date_format") or {}).get("format"),
        "currency_format": {
            "iso_code": (s.get("currency_format") or {}).get("iso_code"),
            "symbol": (s.get("currency_format") or {}).get("symbol"),
            "decimal_digits": (s.get("currency_format") or {}).get("decimal_digits"),
        },
    }
