"""Financial Domain HTTP routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from api.deps import get_db
from financial.constraints import FinancialReport
from financial.events import FinancialEventType, ObligationTier
from financial.models import (
    FinancialAccount,
    FinancialObligation,
)
from financial.policies import (
    shortfall_policy,
    surplus_policy,
    tier1_risk_policy,
)
from financial.projection import available_budget
from financial.service import (
    FinancialConstraintViolation,
    RecordFinancialEventInput,
    current_state,
    record_event,
)

router = APIRouter(
    prefix="/financial",
    tags=["financial"],
    dependencies=[Depends(require_api_key)],
)


# ---------- schemas ----------


class AccountIn(BaseModel):
    name: str
    kind: str = "checking"
    currency: str = "USD"
    opening_balance: float = 0
    minimum_buffer: float = 0


class AccountOut(BaseModel):
    id: str
    name: str
    kind: str
    currency: str
    opening_balance: float
    minimum_buffer: float

    @classmethod
    def from_model(cls, a: FinancialAccount) -> "AccountOut":
        return cls(
            id=a.id,
            name=a.name,
            kind=a.kind,
            currency=a.currency,
            opening_balance=float(a.opening_balance),
            minimum_buffer=float(a.minimum_buffer),
        )


class ObligationIn(BaseModel):
    name: str
    tier: ObligationTier = ObligationTier.TIER_3
    amount: float
    account_id: str
    due_date: datetime
    recurrence_days: int | None = None
    category: str | None = None


class ObligationOut(BaseModel):
    id: str
    name: str
    tier: int
    amount: float
    account_id: str
    due_date: datetime
    recurrence_days: int | None
    category: str | None
    cancelled: bool

    @classmethod
    def from_model(cls, o: FinancialObligation) -> "ObligationOut":
        return cls(
            id=o.id,
            name=o.name,
            tier=o.tier,
            amount=float(o.amount),
            account_id=o.account_id,
            due_date=o.due_date,
            recurrence_days=o.recurrence_days,
            category=o.category,
            cancelled=o.cancelled,
        )


class FinancialEventIn(BaseModel):
    event_type: FinancialEventType
    account_id: str | None = None
    destination_account_id: str | None = None
    obligation_id: str | None = None
    amount: float = 0
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    effective_at: datetime | None = None


class FinancialEventOut(BaseModel):
    id: str
    event_type: str
    account_id: str | None
    destination_account_id: str | None
    obligation_id: str | None
    amount: float
    description: str | None
    metadata: dict[str, Any]
    effective_at: datetime


class StateOut(BaseModel):
    balances: dict[str, float]
    violations: list[dict[str, Any]]
    available_budget: float


# ---------- helpers ----------


def _violation_payload(report: FinancialReport) -> dict[str, Any]:
    return {
        "violations": [
            {
                "code": v.code.value,
                "message": v.message,
                "account_id": v.account_id,
                "obligation_id": v.obligation_id,
            }
            for v in report.violations
        ]
    }


# ---------- accounts ----------


@router.post(
    "/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED
)
async def create_account(
    payload: AccountIn, db: AsyncSession = Depends(get_db)
) -> AccountOut:
    a = FinancialAccount(**payload.model_dump())
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return AccountOut.from_model(a)


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[AccountOut]:
    result = await db.execute(
        select(FinancialAccount).order_by(FinancialAccount.name.asc())
    )
    return [AccountOut.from_model(a) for a in result.scalars()]


# ---------- obligations ----------


@router.post(
    "/obligations",
    response_model=ObligationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_obligation(
    payload: ObligationIn, db: AsyncSession = Depends(get_db)
) -> ObligationOut:
    data = payload.model_dump()
    data["tier"] = int(data["tier"])
    o = FinancialObligation(**data)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return ObligationOut.from_model(o)


@router.get("/obligations", response_model=list[ObligationOut])
async def list_obligations(
    db: AsyncSession = Depends(get_db),
) -> list[ObligationOut]:
    result = await db.execute(
        select(FinancialObligation).order_by(FinancialObligation.due_date.asc())
    )
    return [ObligationOut.from_model(o) for o in result.scalars()]


# ---------- events ----------


@router.post(
    "/events",
    response_model=FinancialEventOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_event(
    payload: FinancialEventIn, db: AsyncSession = Depends(get_db)
) -> FinancialEventOut:
    try:
        event = await record_event(
            db,
            RecordFinancialEventInput(
                event_type=payload.event_type,
                account_id=payload.account_id,
                destination_account_id=payload.destination_account_id,
                obligation_id=payload.obligation_id,
                amount=Decimal(str(payload.amount)),
                description=payload.description,
                metadata=payload.metadata,
                effective_at=payload.effective_at,
            ),
        )
    except FinancialConstraintViolation as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_violation_payload(exc.report),
        )
    await db.commit()
    return FinancialEventOut(
        id=event.id,
        event_type=event.event_type,
        account_id=event.account_id,
        destination_account_id=event.destination_account_id,
        obligation_id=event.obligation_id,
        amount=float(event.amount),
        description=event.description,
        metadata=event.metadata_json,
        effective_at=event.effective_at,
    )


# ---------- projection / state ----------


@router.get("/state", response_model=StateOut)
async def get_state(db: AsyncSession = Depends(get_db)) -> StateOut:
    projection, _accounts, _obligations, report = await current_state(db)
    budget = await available_budget(db)
    return StateOut(
        balances={k: float(v) for k, v in projection.balances.items()},
        violations=_violation_payload(report)["violations"],
        available_budget=float(budget),
    )


@router.get("/recommendations")
async def get_recommendations(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    projection, accounts, obligations, _report = await current_state(db)
    return {
        "surplus": [
            {
                "account_id": s.account_id,
                "account_name": s.account_name,
                "current_balance": float(s.current_balance),
                "minimum_buffer": float(s.minimum_buffer),
                "surplus": float(s.surplus),
            }
            for s in surplus_policy(projection, accounts)
        ],
        "shortfall": [
            {
                "account_id": s.account_id,
                "account_name": s.account_name,
                "minimum_balance": float(s.minimum_balance),
                "minimum_at": s.minimum_at.isoformat() if s.minimum_at else None,
                "minimum_buffer": float(s.minimum_buffer),
            }
            for s in shortfall_policy(projection, accounts)
        ],
        "tier1_risk": [
            {
                "obligation_id": r.obligation_id,
                "obligation_name": r.obligation_name,
                "tier": r.tier,
                "due_date": r.due_date.isoformat(),
                "amount": float(r.amount),
                "projected_balance_at_due": float(r.projected_balance_at_due),
            }
            for r in tier1_risk_policy(projection, obligations)
        ],
    }


@router.get("/budget")
async def get_budget(db: AsyncSession = Depends(get_db)) -> dict[str, float]:
    return {"available_budget": float(await available_budget(db))}
