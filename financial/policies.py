"""Financial Domain policies: surplus/shortfall recommendations.

Policies inspect the projection and emit recommendations. They never execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from financial.models import FinancialAccount, FinancialObligation
from financial.projection import FinancialProjection


@dataclass
class SurplusRecommendation:
    account_id: str
    account_name: str
    current_balance: Decimal
    minimum_buffer: Decimal
    surplus: Decimal


@dataclass
class ShortfallWarning:
    account_id: str
    account_name: str
    minimum_balance: Decimal
    minimum_at: datetime | None
    minimum_buffer: Decimal


@dataclass
class Tier1Risk:
    obligation_id: str
    obligation_name: str
    tier: int
    due_date: datetime
    amount: Decimal
    projected_balance_at_due: Decimal


def surplus_policy(
    projection: FinancialProjection,
    accounts: dict[str, FinancialAccount],
    *,
    surplus_floor: Decimal = Decimal("0"),
) -> list[SurplusRecommendation]:
    out: list[SurplusRecommendation] = []
    for account_id, account in accounts.items():
        balance = projection.balance(account_id)
        buffer = Decimal(str(account.minimum_buffer))
        surplus = balance - buffer
        if surplus > surplus_floor:
            out.append(
                SurplusRecommendation(
                    account_id=account_id,
                    account_name=account.name,
                    current_balance=balance,
                    minimum_buffer=buffer,
                    surplus=surplus,
                )
            )
    return out


def shortfall_policy(
    projection: FinancialProjection,
    accounts: dict[str, FinancialAccount],
) -> list[ShortfallWarning]:
    out: list[ShortfallWarning] = []
    for account_id, account in accounts.items():
        lo, when = projection.min_projected_balance(account_id)
        buffer = Decimal(str(account.minimum_buffer))
        if lo < buffer:
            out.append(
                ShortfallWarning(
                    account_id=account_id,
                    account_name=account.name,
                    minimum_balance=lo,
                    minimum_at=when,
                    minimum_buffer=buffer,
                )
            )
    return out


def tier1_risk_policy(
    projection: FinancialProjection,
    obligations: list[FinancialObligation],
) -> list[Tier1Risk]:
    """Tier-1 obligations whose account's projected balance at due-date can't cover them."""
    out: list[Tier1Risk] = []
    for ob in obligations:
        if ob.tier != 1 or ob.cancelled:
            continue
        # Walk the trajectory up to and including ob.due_date.
        trajectory = projection.trajectories.get(ob.account_id, [])
        balance_at_due: Decimal | None = None
        for entry in trajectory:
            if entry.when <= ob.due_date:
                balance_at_due = entry.balance_after
            else:
                break
        if balance_at_due is None:
            balance_at_due = projection.balance(ob.account_id)
        amount = Decimal(str(ob.amount))
        if balance_at_due < amount:
            out.append(
                Tier1Risk(
                    obligation_id=ob.id,
                    obligation_name=ob.name,
                    tier=ob.tier,
                    due_date=ob.due_date,
                    amount=amount,
                    projected_balance_at_due=balance_at_due,
                )
            )
    return out
