"""
Task / Order model — SCHEMA.md §5 & G2P Pod Missions.
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


class TaskType(str, enum.Enum):
    """Specific task mission types for G2P Pod transport, Sortation, and Auditing."""
    STANDARD = "STANDARD"                    # Generic point-to-point movement
    RETRIEVE_POD = "RETRIEVE_POD"            # Pod slot -> Pick face (Lift pod)
    PICK_ITEM = "PICK_ITEM"                  # Item pick operation at pick face
    RETURN_POD = "RETURN_POD"                # Pick face -> Pod slot (Lower pod)
    AUDIT = "AUDIT"                          # Checkpoint perception patrol
    INDUCT_BATCH = "INDUCT_BATCH"            # Import dock -> Sorting zone batch induction
    DECANT_TO_CHUTE = "DECANT_TO_CHUTE"      # Sorting zone -> Target chute decant
    CONSOLIDATE_EXPORT = "CONSOLIDATE_EXPORT"# Chute -> Export dock batch consolidation
    TRANSFER_TO_SORTATION = "TRANSFER_TO_SORTATION" # Pick station buffer -> Sortation entrance transfer


@dataclass
class Task:
    """
    Represents a warehouse task with full backward compatibility and G2P pod support.
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
    task_type: TaskType = TaskType.STANDARD
    target_shelf_id: Optional[str] = None
    sku_to_pick: Optional[str] = None
    quantity: int = 1
    destination_zone: Optional[str] = None
    pick_station_id: Optional[str] = None

    # Internal tracking
    _pickup_done: bool = field(default=False, repr=False)
    _assigned_tick: Optional[int] = field(default=None, repr=False)
    _completed_tick: Optional[int] = field(default=None, repr=False)

    @property
    def pickup(self) -> Tuple[int, int]:
        return (self.pickup_x, self.pickup_y)

    @pickup.setter
    def pickup(self, val: Tuple[int, int]) -> None:
        self.pickup_x, self.pickup_y = val

    @property
    def dropoff(self) -> Tuple[int, int]:
        return (self.dropoff_x, self.dropoff_y)

    @dropoff.setter
    def dropoff(self, val: Tuple[int, int]) -> None:
        self.dropoff_x, self.dropoff_y = val

    @staticmethod
    def generate_id(prefix: str = "TASK") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
