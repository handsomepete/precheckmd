"""Nox MCP server — exposes YNAB tools (and future integrations) over the
Model Context Protocol via streamable-HTTP transport.

Run locally:
    python mcp_server.py

Or with the MCP CLI:
    mcp run mcp_server.py:mcp

In production (docker-compose), the command is:
    uvicorn mcp_server:asgi_app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import tools.ynab as ynab

mcp = FastMCP("nox")


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — accounts
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_accounts() -> list[dict]:
    """Get all YNAB accounts with current balances."""
    return ynab.get_accounts()


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — transactions (read)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_transactions(
    since: str | None = None,
    before: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    payee_name: str | None = None,
) -> list[dict]:
    """Get YNAB transactions with optional filters.

    Args:
        since: Start date filter YYYY-MM-DD (inclusive).
        before: End date filter YYYY-MM-DD (inclusive).
        account_id: Limit to a single account UUID (from get_accounts).
        category_id: Limit to a single category UUID (from get_categories).
        payee_name: Case-insensitive substring match on payee name.
    """
    return ynab.get_transactions(
        since=since,
        before=before,
        account_id=account_id,
        category_id=category_id,
        payee_name=payee_name,
    )


@mcp.tool()
def get_account_transactions(
    account_id: str,
    since: str | None = None,
    before: str | None = None,
) -> list[dict]:
    """Get transactions for a single YNAB account.

    Args:
        account_id: YNAB account UUID (from get_accounts).
        since: Start date filter YYYY-MM-DD (inclusive).
        before: End date filter YYYY-MM-DD (inclusive).
    """
    return ynab.get_account_transactions(account_id=account_id, since=since, before=before)


@mcp.tool()
def get_transaction(transaction_id: str) -> dict:
    """Get a single YNAB transaction by ID.

    Args:
        transaction_id: YNAB transaction UUID.
    """
    return ynab.get_transaction(transaction_id)


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — transactions (write)
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def create_transaction(
    account_id: str,
    date: str,
    payee_name: str,
    amount: float,
    category_id: str | None = None,
    memo: str | None = None,
) -> dict:
    """Create a new YNAB transaction.

    amount is in dollars: negative = outflow (expense/payment),
    positive = inflow (income/deposit).
    Use get_accounts for account_id, get_categories for category_id.

    Args:
        account_id: YNAB account UUID (from get_accounts).
        date: Transaction date YYYY-MM-DD.
        payee_name: Payee name (max 200 chars).
        amount: Dollar amount. Negative = outflow, positive = inflow.
        category_id: YNAB category UUID (optional — from get_categories).
        memo: Optional memo or note.
    """
    return ynab.create_transaction(
        account_id=account_id,
        date=date,
        payee_name=payee_name,
        amount=amount,
        category_id=category_id,
        memo=memo,
    )


@mcp.tool()
def update_transaction(
    transaction_id: str,
    cleared: str | None = None,
    amount: float | None = None,
    date: str | None = None,
    payee_name: str | None = None,
    memo: str | None = None,
    category_id: str | None = None,
) -> dict:
    """Update an existing YNAB transaction.

    Only provided fields are changed; omitted fields are left as-is.

    Args:
        transaction_id: YNAB transaction UUID.
        cleared: Cleared status — "cleared", "uncleared", or "reconciled".
        amount: New dollar amount. Negative = outflow, positive = inflow.
        date: New date YYYY-MM-DD.
        payee_name: New payee name.
        memo: New memo (pass empty string to clear).
        category_id: New category UUID (from get_categories).
    """
    return ynab.update_transaction(
        transaction_id=transaction_id,
        cleared=cleared,
        amount=amount,
        date=date,
        payee_name=payee_name,
        memo=memo,
        category_id=category_id,
    )


@mcp.tool()
def update_transactions_bulk(transactions: list[dict]) -> dict:
    """Update multiple YNAB transactions in a single API call.

    Each item must have an 'id' field. Only the fields you include are
    updated; omitted fields are left as-is.

    Example:
        [
          {"id": "abc-123", "cleared": "cleared"},
          {"id": "def-456", "amount": -42.50, "memo": "corrected"}
        ]

    Args:
        transactions: List of patch objects. Required: id. Optional:
            cleared ("cleared"/"uncleared"/"reconciled"), amount (dollars),
            date (YYYY-MM-DD), payee_name, memo, category_id.
    """
    return ynab.update_transactions_bulk(transactions)


@mcp.tool()
def delete_transaction(transaction_id: str) -> dict:
    """Delete a YNAB transaction permanently.

    Args:
        transaction_id: YNAB transaction UUID to delete.
    """
    return ynab.delete_transaction(transaction_id)


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — scheduled transactions
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def create_scheduled_transaction(
    account_id: str,
    date: str,
    frequency: str,
    amount: float,
    payee_name: str | None = None,
    category_id: str | None = None,
    memo: str | None = None,
) -> dict:
    """Create a new YNAB scheduled (recurring) transaction.

    amount is in dollars: negative = outflow, positive = inflow.

    Args:
        account_id: YNAB account UUID (from get_accounts).
        date: First occurrence date YYYY-MM-DD.
        frequency: Recurrence — "never", "daily", "weekly", "monthly", or "yearly".
        amount: Dollar amount. Negative = outflow, positive = inflow.
        payee_name: Payee name (optional).
        category_id: YNAB category UUID (optional).
        memo: Optional memo.
    """
    return ynab.create_scheduled_transaction(
        account_id=account_id,
        date=date,
        frequency=frequency,
        amount=amount,
        payee_name=payee_name,
        category_id=category_id,
        memo=memo,
    )


@mcp.tool()
def get_scheduled_transactions() -> list[dict]:
    """Get upcoming YNAB scheduled transactions."""
    return ynab.get_scheduled_transactions()


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — categories
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_categories(month: str | None = None) -> list[dict]:
    """Get YNAB categories with budgeted/activity/balance.

    Args:
        month: YYYY-MM, defaults to current month's category list without
               balance data. Provide a month to get live budget figures.
    """
    return ynab.get_categories(month=month)


@mcp.tool()
def update_category_budget(
    month: str,
    category_id: str,
    budgeted: float,
) -> dict:
    """Set the budgeted dollar amount for a category in a given month.

    Args:
        month: Target month in YYYY-MM format.
        category_id: YNAB category UUID (from get_categories).
        budgeted: Dollar amount to budget for this category this month.
    """
    return ynab.update_category_budget(month=month, category_id=category_id, budgeted=budgeted)


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — payees
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_payees() -> list[dict]:
    """Get all YNAB payees."""
    return ynab.get_payees()


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — months / budget summary
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_finances() -> dict:
    """Get YNAB budget snapshot — current-month income, budgeted, TBB, and
    overspent categories."""
    return ynab.get_finances()


@mcp.tool()
def get_months() -> list[dict]:
    """Get month-by-month YNAB budget summary history."""
    return ynab.get_months()


@mcp.tool()
def get_month(month: str) -> dict:
    """Get full detail for a single budget month, including all category balances.

    Args:
        month: Target month in YYYY-MM format (e.g. "2025-06").
    """
    return ynab.get_month(month)


# ═══════════════════════════════════════════════════════════════════════════
# YNAB — settings
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_budget_settings() -> dict:
    """Get budget-level settings: date format and currency format."""
    return ynab.get_budget_settings()


# ═══════════════════════════════════════════════════════════════════════════
# ASGI app (for production: uvicorn mcp_server:asgi_app)
# ═══════════════════════════════════════════════════════════════════════════

asgi_app = mcp.streamable_http_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(asgi_app, host="0.0.0.0", port=8001)
