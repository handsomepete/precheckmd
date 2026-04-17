"""Financial Domain service layer: validate-then-execute path for all writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from financial.constraints import (
    FinancialReport,
    evaluate_event,
    evaluate_state,
)
from financial.events import FinancialEventType
from financial.models import (
    FinancialAccount,
    FinancialEvent,
    FinancialObligation,
)
from financial.projection import (
    FinancialProjection,
    build_projection,
    load_accounts,
    load_obligations,
)


class FinancialConstraintViolation(Exception):
    def __init__(self, report: FinancialReport) -> None:
        super().__init__(
            "financial constraint violation: "
            + "; ".join(v.message for v in report.violations)
        )
        self.report = report


@dataclass
class RecordFinancialEventInput:
    event_type: FinancialEventType
    account_id: str | None = None
    destination_account_id: str | None = None
    obligation_id: str | None = None
    amount: Decimal = Decimal("0")
    description: str | None = None
    metadata: dict[str, Any] | None = None
    effective_at: datetime | None = None


async def record_event(
    session: AsyncSession, payload: RecordFinancialEventInput
) -> FinancialEvent:
    accounts = await load_accounts(session)
    obligations = await load_obligations(session)
    projection = await build_projection(session)

    candidate = FinancialEvent(
        event_type=payload.event_type.value,
        account_id=payload.account_id,
        destination_account_id=payload.destination_account_id,
        obligation_id=payload.obligation_id,
        amount=payload.amount,
        description=payload.description,
        metadata_json=payload.metadata or {},
        effective_at=payload.effective_at or datetime.now(timezone.utc),
    )

    report = evaluate_event(projection, candidate, accounts, obligations)
    if not report.ok:
        raise FinancialConstraintViolation(report)

    session.add(candidate)
    await session.flush()
    return candidate


async def current_state(
    session: AsyncSession,
) -> tuple[
    FinancialProjection,
    dict[str, FinancialAccount],
    list[FinancialObligation],
    FinancialReport,
]:
    accounts = await load_accounts(session)
    obligations = await load_obligations(session)
    projection = await build_projection(session)
    report = evaluate_state(projection, accounts, obligations)
    return projection, accounts, obligations, report
