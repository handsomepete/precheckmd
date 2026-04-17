"""Financial Domain event types."""

from __future__ import annotations

from enum import Enum


class FinancialEventType(str, Enum):
    ACCOUNT_OPENED = "ACCOUNT_OPENED"
    FUNDS_DEPOSITED = "FUNDS_DEPOSITED"
    FUNDS_WITHDRAWN = "FUNDS_WITHDRAWN"
    TRANSFER = "TRANSFER"
    OBLIGATION_SCHEDULED = "OBLIGATION_SCHEDULED"
    OBLIGATION_PAID = "OBLIGATION_PAID"
    OBLIGATION_CANCELLED = "OBLIGATION_CANCELLED"
    BALANCE_RECONCILED = "BALANCE_RECONCILED"


CASH_MOVEMENT_EVENTS = frozenset(
    {
        FinancialEventType.FUNDS_DEPOSITED,
        FinancialEventType.FUNDS_WITHDRAWN,
        FinancialEventType.TRANSFER,
        FinancialEventType.OBLIGATION_PAID,
        FinancialEventType.BALANCE_RECONCILED,
    }
)


class ObligationTier(int, Enum):
    """Tier-1 = must be met (hard constraint). Tier-2 = should. Tier-3 = nice."""

    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
