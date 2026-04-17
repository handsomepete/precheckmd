"""Physical Domain event types.

The Physical Domain is event sourced: the projection (current truth) is derived
from the ordered sequence of events. No direct mutation of projected state.
"""

from __future__ import annotations

from enum import Enum


class PhysicalEventType(str, Enum):
    ADD_ITEM = "ADD_ITEM"
    REMOVE_ITEM = "REMOVE_ITEM"
    ITEM_CONSUMED = "ITEM_CONSUMED"
    ITEM_EXPIRED = "ITEM_EXPIRED"
    MOVE_ITEM = "MOVE_ITEM"
    PROCUREMENT_REQUESTED = "PROCUREMENT_REQUESTED"
    PROCUREMENT_APPROVED = "PROCUREMENT_APPROVED"


# Events that mutate inventory quantity / location
INVENTORY_MUTATING_EVENTS = frozenset(
    {
        PhysicalEventType.ADD_ITEM,
        PhysicalEventType.REMOVE_ITEM,
        PhysicalEventType.ITEM_CONSUMED,
        PhysicalEventType.ITEM_EXPIRED,
        PhysicalEventType.MOVE_ITEM,
    }
)

# Events that pertain to the procurement workflow
PROCUREMENT_EVENTS = frozenset(
    {
        PhysicalEventType.PROCUREMENT_REQUESTED,
        PhysicalEventType.PROCUREMENT_APPROVED,
    }
)
