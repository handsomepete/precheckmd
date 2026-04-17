"""Financial Domain hard constraints.

- All Tier-1 obligations must be met
- No future liquidity breach allowed (per-account: trajectory >= minimum_buffer)
- No negative event amounts; transfers must have a destination
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from financial.events import FinancialEventType
from financial.models import FinancialAccount, FinancialEvent, FinancialObligation
from financial.policies import tier1_risk_policy
from financial.projection import FinancialProjection, _apply_event_to_balances


class FinancialViolationCode(str, Enum):
    LIQUIDITY_BREACH = "LIQUIDITY_BREACH"
    TIER1_OBLIGATION_AT_RISK = "TIER1_OBLIGATION_AT_RISK"
    NEGATIVE_AMOUNT = "NEGATIVE_AMOUNT"
    INVALID_TRANSFER = "INVALID_TRANSFER"
    UNKNOWN_ACCOUNT = "UNKNOWN_ACCOUNT"
    UNKNOWN_OBLIGATION = "UNKNOWN_OBLIGATION"


@dataclass
class FinancialViolation:
    code: FinancialViolationCode
    message: str
    account_id: str | None = None
    obligation_id: str | None = None


@dataclass
class FinancialReport:
    violations: list[FinancialViolation]

    @property
    def ok(self) -> bool:
        return not self.violations


def _check_liquidity(
    projection: FinancialProjection, accounts: dict[str, FinancialAccount]
) -> list[FinancialViolation]:
    out: list[FinancialViolation] = []
    for account_id, account in accounts.items():
        lo, when = projection.min_projected_balance(account_id)
        buffer = Decimal(str(account.minimum_buffer))
        if lo < buffer:
            out.append(
                FinancialViolation(
                    code=FinancialViolationCode.LIQUIDITY_BREACH,
                    message=(
                        f"account '{account.name}' projected to {lo} "
                        f"(buffer {buffer}) at {when}"
                    ),
                    account_id=account_id,
                )
            )
    return out


def _check_tier1(
    projection: FinancialProjection, obligations: list[FinancialObligation]
) -> list[FinancialViolation]:
    out: list[FinancialViolation] = []
    for risk in tier1_risk_policy(projection, obligations):
        out.append(
            FinancialViolation(
                code=FinancialViolationCode.TIER1_OBLIGATION_AT_RISK,
                message=(
                    f"tier-1 obligation '{risk.obligation_name}' due "
                    f"{risk.due_date}: needs {risk.amount}, projected balance "
                    f"{risk.projected_balance_at_due}"
                ),
                obligation_id=risk.obligation_id,
                account_id=None,
            )
        )
    return out


def evaluate_state(
    projection: FinancialProjection,
    accounts: dict[str, FinancialAccount],
    obligations: list[FinancialObligation],
) -> FinancialReport:
    violations: list[FinancialViolation] = []
    violations.extend(_check_liquidity(projection, accounts))
    violations.extend(_check_tier1(projection, obligations))
    return FinancialReport(violations=violations)


def _structural_checks(
    event: FinancialEvent,
    accounts: dict[str, FinancialAccount],
    obligations: dict[str, FinancialObligation],
) -> list[FinancialViolation]:
    violations: list[FinancialViolation] = []
    if Decimal(str(event.amount)) < 0:
        violations.append(
            FinancialViolation(
                code=FinancialViolationCode.NEGATIVE_AMOUNT,
                message="event amount is negative",
            )
        )
    if event.account_id is not None and event.account_id not in accounts:
        violations.append(
            FinancialViolation(
                code=FinancialViolationCode.UNKNOWN_ACCOUNT,
                message=f"unknown account_id {event.account_id}",
                account_id=event.account_id,
            )
        )
    if (
        event.destination_account_id is not None
        and event.destination_account_id not in accounts
    ):
        violations.append(
            FinancialViolation(
                code=FinancialViolationCode.UNKNOWN_ACCOUNT,
                message=f"unknown destination_account_id {event.destination_account_id}",
                account_id=event.destination_account_id,
            )
        )
    if event.event_type == FinancialEventType.TRANSFER:
        if not event.account_id or not event.destination_account_id:
            violations.append(
                FinancialViolation(
                    code=FinancialViolationCode.INVALID_TRANSFER,
                    message="TRANSFER requires both account_id and destination_account_id",
                )
            )
        elif event.account_id == event.destination_account_id:
            violations.append(
                FinancialViolation(
                    code=FinancialViolationCode.INVALID_TRANSFER,
                    message="TRANSFER source and destination are identical",
                )
            )
    if event.obligation_id is not None and event.obligation_id not in obligations:
        violations.append(
            FinancialViolation(
                code=FinancialViolationCode.UNKNOWN_OBLIGATION,
                message=f"unknown obligation_id {event.obligation_id}",
                obligation_id=event.obligation_id,
            )
        )
    return violations


def evaluate_event(
    projection: FinancialProjection,
    event: FinancialEvent,
    accounts: dict[str, FinancialAccount],
    obligations: list[FinancialObligation],
) -> FinancialReport:
    """Validate a candidate event by simulating it on a copy of the projection."""
    obligations_by_id = {ob.id: ob for ob in obligations}
    pre = _structural_checks(event, accounts, obligations_by_id)
    if pre:
        return FinancialReport(violations=pre)

    hypothetical = FinancialProjection(
        balances=dict(projection.balances),
        trajectories={
            k: list(v) for k, v in projection.trajectories.items()
        },
    )
    deltas = _apply_event_to_balances(hypothetical.balances, event)
    from financial.projection import CashflowEntry  # local import to avoid cycle

    for account_id, delta in deltas:
        hypothetical.trajectories.setdefault(account_id, []).append(
            CashflowEntry(
                when=event.effective_at,
                account_id=account_id,
                delta=delta,
                balance_after=hypothetical.balances[account_id],
                source="event",
                reference_id=event.id or "candidate",
                description=event.description or event.event_type,
            )
        )
        hypothetical.trajectories[account_id].sort(key=lambda e: e.when)
        # Re-sorting can move the new entry into the past; recompute
        # balance_after by replaying deltas in time order.
        running = Decimal("0")
        for entry in hypothetical.trajectories[account_id]:
            running += entry.delta
            entry.balance_after = running
        hypothetical.balances[account_id] = running

    return evaluate_state(hypothetical, accounts, obligations)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
