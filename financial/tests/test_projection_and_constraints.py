"""In-process tests for the Financial Domain (no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from financial.constraints import (
    FinancialViolationCode,
    evaluate_event,
    evaluate_state,
)
from financial.events import FinancialEventType, ObligationTier
from financial.models import (
    FinancialAccount,
    FinancialEvent,
    FinancialObligation,
)
from financial.policies import (
    shortfall_policy,
    surplus_policy,
    tier1_risk_policy,
)
from financial.projection import (
    CashflowEntry,
    FinancialProjection,
    _apply_event_to_balances,
)


NOW = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)


def _account(**kw) -> FinancialAccount:
    kw.setdefault("name", "checking")
    kw.setdefault("kind", "checking")
    kw.setdefault("currency", "USD")
    kw.setdefault("opening_balance", Decimal("1000"))
    kw.setdefault("minimum_buffer", Decimal("0"))
    a = FinancialAccount(**kw)
    if "id" not in kw:
        a.id = "acct-" + kw["name"]
    a.created_at = NOW - timedelta(days=30)
    return a


def _obligation(**kw) -> FinancialObligation:
    kw.setdefault("name", "rent")
    kw.setdefault("tier", 1)
    kw.setdefault("amount", Decimal("500"))
    kw.setdefault("account_id", "acct-checking")
    kw.setdefault("due_date", NOW + timedelta(days=15))
    kw.setdefault("recurrence_days", None)
    kw.setdefault("category", None)
    kw.setdefault("cancelled", False)
    o = FinancialObligation(**kw)
    if "id" not in kw:
        o.id = "ob-" + kw["name"]
    return o


def _event(**kw) -> FinancialEvent:
    kw.setdefault("metadata_json", {})
    kw.setdefault("amount", Decimal("0"))
    kw.setdefault("effective_at", NOW)
    if isinstance(kw.get("event_type"), FinancialEventType):
        kw["event_type"] = kw["event_type"].value
    return FinancialEvent(**kw)


def _projection_from(
    accounts: list[FinancialAccount],
) -> FinancialProjection:
    proj = FinancialProjection()
    for a in accounts:
        opening = Decimal(str(a.opening_balance))
        proj.balances[a.id] = opening
        proj.trajectories[a.id] = [
            CashflowEntry(
                when=a.created_at,
                account_id=a.id,
                delta=opening,
                balance_after=opening,
                source="event",
                reference_id=a.id,
                description="opening balance",
            )
        ]
    return proj


def test_deposit_increases_balance() -> None:
    a = _account(opening_balance=Decimal("100"))
    proj = _projection_from([a])
    deltas = _apply_event_to_balances(
        proj.balances,
        _event(
            event_type=FinancialEventType.FUNDS_DEPOSITED,
            account_id=a.id,
            amount=Decimal("50"),
        ),
    )
    assert proj.balances[a.id] == Decimal("150")
    assert deltas == [(a.id, Decimal("50"))]


def test_withdrawal_blocked_when_it_would_breach_buffer() -> None:
    a = _account(opening_balance=Decimal("200"), minimum_buffer=Decimal("100"))
    proj = _projection_from([a])
    # Withdraw 150 would leave 50, below buffer 100 -> liquidity breach.
    candidate = _event(
        event_type=FinancialEventType.FUNDS_WITHDRAWN,
        account_id=a.id,
        amount=Decimal("150"),
        effective_at=NOW,
    )
    report = evaluate_event(proj, candidate, {a.id: a}, [])
    assert not report.ok
    assert any(
        v.code == FinancialViolationCode.LIQUIDITY_BREACH
        for v in report.violations
    )


def test_withdrawal_allowed_when_buffer_respected() -> None:
    a = _account(opening_balance=Decimal("500"), minimum_buffer=Decimal("100"))
    proj = _projection_from([a])
    candidate = _event(
        event_type=FinancialEventType.FUNDS_WITHDRAWN,
        account_id=a.id,
        amount=Decimal("200"),
    )
    report = evaluate_event(proj, candidate, {a.id: a}, [])
    assert report.ok


def test_tier1_obligation_at_risk_blocks_withdrawal() -> None:
    a = _account(opening_balance=Decimal("600"), minimum_buffer=Decimal("0"))
    proj = _projection_from([a])
    # Existing tier-1 obligation of 500 in 15d.
    rent = _obligation(name="rent", tier=1, amount=Decimal("500"))
    # Add the projected obligation hit so the trajectory shows it.
    proj.balances[a.id] = Decimal("100")
    proj.trajectories[a.id].append(
        CashflowEntry(
            when=rent.due_date,
            account_id=a.id,
            delta=Decimal("-500"),
            balance_after=Decimal("100"),
            source="obligation",
            reference_id=rent.id,
            description=rent.name,
        )
    )
    # Now try to withdraw 200 today; rent would then go to -100 (tier-1 at risk).
    candidate = _event(
        event_type=FinancialEventType.FUNDS_WITHDRAWN,
        account_id=a.id,
        amount=Decimal("200"),
    )
    report = evaluate_event(proj, candidate, {a.id: a}, [rent])
    assert not report.ok
    codes = {v.code for v in report.violations}
    assert FinancialViolationCode.TIER1_OBLIGATION_AT_RISK in codes


def test_transfer_requires_distinct_accounts() -> None:
    a = _account(opening_balance=Decimal("100"))
    proj = _projection_from([a])
    candidate = _event(
        event_type=FinancialEventType.TRANSFER,
        account_id=a.id,
        destination_account_id=a.id,
        amount=Decimal("10"),
    )
    report = evaluate_event(proj, candidate, {a.id: a}, [])
    assert any(
        v.code == FinancialViolationCode.INVALID_TRANSFER for v in report.violations
    )


def test_negative_amount_rejected() -> None:
    a = _account()
    proj = _projection_from([a])
    candidate = _event(
        event_type=FinancialEventType.FUNDS_DEPOSITED,
        account_id=a.id,
        amount=Decimal("-1"),
    )
    report = evaluate_event(proj, candidate, {a.id: a}, [])
    assert any(
        v.code == FinancialViolationCode.NEGATIVE_AMOUNT for v in report.violations
    )


def test_unknown_account_rejected() -> None:
    candidate = _event(
        event_type=FinancialEventType.FUNDS_DEPOSITED,
        account_id="ghost",
        amount=Decimal("1"),
    )
    report = evaluate_event(_projection_from([]), candidate, {}, [])
    assert any(
        v.code == FinancialViolationCode.UNKNOWN_ACCOUNT for v in report.violations
    )


def test_surplus_policy_emits_above_buffer() -> None:
    a = _account(opening_balance=Decimal("1000"), minimum_buffer=Decimal("200"))
    proj = _projection_from([a])
    surplus = surplus_policy(proj, {a.id: a})
    assert len(surplus) == 1
    assert surplus[0].surplus == Decimal("800")


def test_shortfall_policy_emits_when_trajectory_dips() -> None:
    a = _account(opening_balance=Decimal("100"), minimum_buffer=Decimal("50"))
    proj = _projection_from([a])
    proj.balances[a.id] = Decimal("40")
    proj.trajectories[a.id].append(
        CashflowEntry(
            when=NOW + timedelta(days=1),
            account_id=a.id,
            delta=Decimal("-60"),
            balance_after=Decimal("40"),
            source="event",
            reference_id="x",
            description="bill",
        )
    )
    warnings = shortfall_policy(proj, {a.id: a})
    assert len(warnings) == 1
    assert warnings[0].minimum_balance == Decimal("40")


def test_tier1_risk_policy_lists_underfunded_obligations() -> None:
    a = _account(opening_balance=Decimal("100"))
    proj = _projection_from([a])
    risky = _obligation(name="mortgage", tier=1, amount=Decimal("500"))
    risks = tier1_risk_policy(proj, [risky])
    assert len(risks) == 1
    assert risks[0].obligation_name == "mortgage"


def test_state_passes_when_no_violations() -> None:
    a = _account(opening_balance=Decimal("1000"), minimum_buffer=Decimal("0"))
    proj = _projection_from([a])
    report = evaluate_state(proj, {a.id: a}, [])
    assert report.ok
