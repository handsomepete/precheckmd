"""Operational Domain event types."""

from __future__ import annotations

from enum import Enum


class OperationalEventType(str, Enum):
    RESOURCE_REGISTERED = "RESOURCE_REGISTERED"
    TASK_CREATED = "TASK_CREATED"
    TASK_SCHEDULED = "TASK_SCHEDULED"
    TASK_RESCHEDULED = "TASK_RESCHEDULED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_CANCELLED = "TASK_CANCELLED"


class TaskStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
