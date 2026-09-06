"""
models.py — Data models for the Conflict Negotiation & Arbitration Engine.
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
SIH26123 — Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses.

Deliverable 1:
Defines RobotState, Heading, Robot, and Task data models.
Designed to be 100% interoperable with both Member 2 pathfinding dict paths
and Member 4 backend simulation internal state representations.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class RobotState(str, enum.Enum):
    IDLE = "IDLE"
    EN_ROUTE = "EN_ROUTE"
    CONFLICT_NEGOTIATING = "CONFLICT_NEGOTIATING"
    CHARGING = "CHARGING"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class Heading(str, enum.Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


@dataclass
class Robot:
    """Robot state entity for conflict arbitration and motion management."""
    robot_id: str
    position: Tuple[int, int]
    heading: Heading
    state: RobotState
    battery_pct: float
    current_task_id: Optional[str]
    path: List[Dict[str, Any]] = field(default_factory=list)
    priority_score: float = 0.0
    wait_ticks_so_far: int = 0
    last_updated_tick: int = 0

    @property
    def x(self) -> int:
        """Convenience X coordinate accessor."""
        return self.position[0]

    @property
    def y(self) -> int:
        """Convenience Y coordinate accessor."""
        return self.position[1]

    def distance_to_goal(self) -> int:
        """Manhattan distance from current position to the final destination node"""
        if not self.path:
            return 0
        dest = self.path[-1]
        dest_x = dest["x"] if isinstance(dest, dict) or hasattr(dest, "__getitem__") else dest.x
        dest_y = dest["y"] if isinstance(dest, dict) or hasattr(dest, "__getitem__") else dest.y
        return abs(dest_x - self.position[0]) + abs(dest_y - self.position[1])


@dataclass
class Task:
    """Represents an atomic pickup-to-dropoff mission."""
    task_id: str
    pickup: Tuple[int, int]
    dropoff: Tuple[int, int]
    urgency: int  # 1 to 5
    created_tick: int
    assigned_robot_id: Optional[str] = None
    status: str = "PENDING"  # PENDING, ASSIGNED, IN_PROGRESS, COMPLETED
    payload_weight_kg: float = 0.0

    @property
    def pickup_x(self) -> int:
        return self.pickup[0]

    @property
    def pickup_y(self) -> int:
        return self.pickup[1]

    @property
    def dropoff_x(self) -> int:
        return self.dropoff[0]

    @property
    def dropoff_y(self) -> int:
        return self.dropoff[1]