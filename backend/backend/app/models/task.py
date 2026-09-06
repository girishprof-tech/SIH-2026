"""
Task / Order model — SCHEMA.md §5.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Optional, Tuple


class TaskStatus(str, enum.Enum):
    """SCHEMA.md §5 — Status Values."""
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


@dataclass
class Task:
    """
    Represents a warehouse task (pickup → dropoff).

    Fields are identical to SCHEMA.md §5.
    """

    task_id: str
    pickup_x: int
    pickup_y: int
    dropoff_x: int
    dropoff_y: int
    urgency: int                     # 1–5
    created_tick: int
    status: TaskStatus = TaskStatus.PENDING
    assigned_robot_id: Optional[str] = None
    payload_weight_kg: float = 0.0

    # Internal tracking
    _pickup_done: bool = field(default=False, repr=False)
    _assigned_tick: Optional[int] = field(default=None, repr=False)
    _completed_tick: Optional[int] = field(default=None, repr=False)

    @property
    def pickup(self) -> Tuple[int, int]:
        return (self.pickup_x, self.pickup_y)

    @property
    def dropoff(self) -> Tuple[int, int]:
        return (self.dropoff_x, self.dropoff_y)

    @staticmethod
    def generate_id() -> str:
        return f"TASK-{uuid.uuid4().hex[:6].upper()}"
