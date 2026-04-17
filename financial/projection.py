"""Projection of the financial event log + scheduled obligations into balances.

Two views:
- current balances: per account, derived from events with effective_at <= now
- projected cashflow: per account, walking forward through future events plus
  expanded obligations across a horizon, returning a balance trajectory

Projection defines truth. No direct mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial.events import FinancialEventType
from financial.models import (
    FinancialAccount,
    FinancialEvent,
    FinancialObligation,
)


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class CashflowEntry:
    when: datetime
    account_id: str
    delta: Decimal
    balance_after: Decimal
    source: str  # "event" | "obligation"
    reference_id: str | None
    description: str


@dataclass
class FinancialProjection:
    balances: dict[str, Decimal] = field(default_factory=dict)
    # Per-account trajectory of (when, balance_after) sorted ascending.
    trajectories: dict[str, list[CashflowEntry]] = field(default_factory=dict)

    def balance(self, account_id: str) -> Decimal:
        return self.balances.get(account_id, Decimal("0"))

    def min_projected_balance(
        self, account_id: str, *, after: datetime | None = None
    ) -> tuple[Decimal, datetime | None]:
        """Return (lowest balance, when) for an account across its trajectory."""
        trajectory = self.trajectories.get(account_id, [])
        lo: Decimal | None = None
        when: datetime | None = None
        for entry in trajectory:
            if after is not None and entry.when < after:
                continue
            if lo is None or entry.balance_after < lo:
                lo = entry.balance_after
                when = entry.when
        if lo is None:
            return self.balance(account_id), None
        return lo, when


def _apply_event_to_balances(
    balances: dict[str, Decimal], event: FinancialEvent
) -> list[tuple[str, Decimal]]:
    """Apply a single event to balances. Returns list of (account_id, delta)."""
    etype = event.event_type
    amount = _to_decimal(event.amount)
    deltas: list[tuple[str, Decimal]] = []

    if etype == FinancialEventType.ACCOUNT_OPENED:
        # ACCOUNT_OPENED is informational; opening balance is recorded on the
        # account row itself and seeded into the projection separately.
        return deltas

    if etype == FinancialEventType.FUNDS_DEPOSITED:
        if event.account_id:
            deltas.append((event.account_id, amount))
    elif etype in (
        FinancialEventType.FUNDS_WITHDRAWN,
        FinancialEventType.OBLIGATION_PAID,
    ):
        if event.account_id:
            deltas.append((event.account_id, -amount))
    elif etype == FinancialEventType.TRANSFER:
        if event.account_id:
            deltas.append((event.account_id, -amount))
        if event.destination_account_id:
            deltas.append((event.destination_account_id, amount))
    elif etype == FinancialEventType.BALANCE_RECONCILED:
        if event.account_id:
            current = balances.get(event.account_id, Decimal("0"))
            deltas.append((event.account_id, amount - current))

    for account_id, delta in deltas:
        balances[account_id] = balances.get(account_id, Decimal("0")) + delta
    return deltas


def _expand_obligations(
    obligations: list[FinancialObligation],
    *,
    horizon_end: datetime,
    after: datetime,
) -> list[tuple[datetime, FinancialObligation]]:
    """Materialize obligation occurrences (recurring or one-shot) within [after, horizon_end]."""
    out: list[tuple[datetime, FinancialObligation]] = []
    for ob in obligations:
        if ob.cancelled:
            continue
        when = ob.due_date
        if ob.recurrence_days and ob.recurrence_days > 0:
            # Skip past occurrences efficiently.
            if when < after:
                gap = after - when
                steps = int(gap.total_seconds() // (ob.recurrence_days * 86400)) + 1
                when = when + timedelta(days=ob.recurrence_days * steps)
            while when <= horizon_end:
                if when >= after:
                    out.append((when, ob))
                when = when + timedelta(days=ob.recurrence_days)
        else:
            if after <= when <= horizon_end:
                out.append((when, ob))
    return out


async def build_projection(
    session: AsyncSession,
    *,
    horizon_days: int = 90,
    as_of: datetime | None = None,
) -> FinancialProjection:
    """Build the financial projection through ``as_of + horizon_days``.

    Replays:
    1. Account opening balances (seeded from FinancialAccount rows).
    2. All events with effective_at <= horizon_end, in order.
    3. Future occurrences of non-cancelled obligations within the horizon
       that don't already have a corresponding OBLIGATION_PAID event.
    """
    as_of = as_of or datetime.now(timezone.utc)
    horizon_end = as_of + timedelta(days=horizon_days)

    accounts = (await session.execute(select(FinancialAccount))).scalars().all()
    obligations = (
        (await session.execute(select(FinancialObligation))).scalars().all()
    )
    events = (
        (
            await session.execute(
                select(FinancialEvent)
                .where(FinancialEvent.effective_at <= horizon_end)
                .order_by(
                    FinancialEvent.effective_at.asc(),
                    FinancialEvent.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )

    balances: dict[str, Decimal] = {
        a.id: _to_decimal(a.opening_balance) for a in accounts
    }
    trajectories: dict[str, list[CashflowEntry]] = {a.id: [] for a in accounts}

    # Seed trajectory with opening balance points (at account creation time).
    for a in accounts:
        trajectories[a.id].append(
            CashflowEntry(
                when=a.created_at or as_of,
                account_id=a.id,
                delta=_to_decimal(a.opening_balance),
                balance_after=_to_decimal(a.opening_balance),
                source="event",
                reference_id=a.id,
                description="opening balance",
            )
        )

    paid_obligation_occurrences: set[tuple[str, datetime]] = set()
    for ev in events:
        if (
            ev.event_type == FinancialEventType.OBLIGATION_PAID
            and ev.obligation_id is not None
        ):
            paid_obligation_occurrences.add((ev.obligation_id, ev.effective_at))

        deltas = _apply_event_to_balances(balances, ev)
        for account_id, delta in deltas:
            trajectories.setdefault(account_id, []).append(
                CashflowEntry(
                    when=ev.effective_at,
                    account_id=account_id,
                    delta=delta,
                    balance_after=balances[account_id],
                    source="event",
                    reference_id=ev.id,
                    description=ev.description or ev.event_type,
                )
            )

    expanded = _expand_obligations(
        list(obligations), horizon_end=horizon_end, after=as_of
    )
    expanded.sort(key=lambda t: t[0])
    for when, ob in expanded:
        # Skip occurrences that already have a matching paid event.
        if (ob.id, when) in paid_obligation_occurrences:
            continue
        amount = _to_decimal(ob.amount)
        balances[ob.account_id] = (
            balances.get(ob.account_id, Decimal("0")) - amount
        )
        trajectories.setdefault(ob.account_id, []).append(
            CashflowEntry(
                when=when,
                account_id=ob.account_id,
                delta=-amount,
                balance_after=balances[ob.account_id],
                source="obligation",
                reference_id=ob.id,
                description=f"{ob.name} (tier {ob.tier})",
            )
        )

    for account_id in trajectories:
        trajectories[account_id].sort(key=lambda e: e.when)

    return FinancialProjection(balances=balances, trajectories=trajectories)


async def load_accounts(session: AsyncSession) -> dict[str, FinancialAccount]:
    result = await session.execute(select(FinancialAccount))
    return {a.id: a for a in result.scalars()}


async def load_obligations(session: AsyncSession) -> list[FinancialObligation]:
    result = await session.execute(select(FinancialObligation))
    return list(result.scalars())


async def available_budget(
    session: AsyncSession,
    *,
    horizon_days: int = 90,
    as_of: datetime | None = None,
) -> Decimal:
    """Aggregate liquidity available without breaching any account's minimum buffer.

    Sum across accounts of (current_balance - minimum_buffer). Negative
    contributions floor to zero. This is the ceiling the financial domain
    will permit other domains to spend right now without further analysis.
    """
    projection = await build_projection(
        session, horizon_days=horizon_days, as_of=as_of
    )
    accounts = await load_accounts(session)
    total = Decimal("0")
    for account_id, account in accounts.items():
        balance = projection.balance(account_id)
        buffer = _to_decimal(account.minimum_buffer)
        headroom = balance - buffer
        if headroom > 0:
            total += headroom
    return total
