"""
Robot internal state model.

Exact fields are defined by SCHEMA.md §4.
This is the AUTHORITATIVE in-memory representation — not a DB model.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


class RobotState(str, enum.Enum):
    """SCHEMA.md §4 — Robot States."""
    IDLE = "IDLE"
    EN_ROUTE = "EN_ROUTE"
    CONFLICT_NEGOTIATING = "CONFLICT_NEGOTIATING"
    CHARGING = "CHARGING"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class Heading(str, enum.Enum):
    """SCHEMA.md §4 — Heading Values."""
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


# Heading → (dx, dy) movement delta (SCHEMA.md: +x=East, +y=South)
HEADING_DELTA: dict[Heading, Tuple[int, int]] = {
    Heading.NORTH: (0, -1),
    Heading.SOUTH: (0, 1),
    Heading.EAST: (1, 0),
    Heading.WEST: (-1, 0),
}

# Opposite headings (for swap-collision detection)
OPPOSITE_HEADING: dict[Heading, Heading] = {
    Heading.NORTH: Heading.SOUTH,
    Heading.SOUTH: Heading.NORTH,
    Heading.EAST: Heading.WEST,
    Heading.WEST: Heading.EAST,
}


@dataclass
class PathNode:
    """One step on a planned path — (x, y, t)."""
    x: int
    y: int
    t: int

    def __getitem__(self, item: str):
        if item == "x":
            return self.x
        elif item == "y":
            return self.y
        elif item == "t":
            return self.t
        raise KeyError(item)


@dataclass
class Robot:
    """
    Authoritative mutable robot state.

    All fields correspond 1-to-1 with SCHEMA.md §4.
    Additional internal-only fields are prefixed with an underscore.
    """

    robot_id: str
    x: int
    y: int
    heading: Heading
    state: RobotState
    battery_pct: float
    current_task_id: Optional[str]
    priority_score: int
    last_updated_tick: int

    # Planned path — list of PathNode
    path: List[PathNode] = field(default_factory=list)

    # ── Internal-only fields (not exposed in WebSocket contract) ─────────────
    # Path index: next step to execute
    _path_idx: int = field(default=0, repr=False)

    # Number of ticks this robot has been waiting (used in priority formula)
    _wait_ticks: int = field(default=0, repr=False)

    # If True, robot is turning this tick (occupies current cell, no move)
    _is_turning: bool = field(default=False, repr=False)

    # Target heading when turning
    _target_heading: Optional[Heading] = field(default=None, repr=False)

    # Pickup/drop operation countdown (ticks remaining)
    _operation_ticks_remaining: int = field(default=0, repr=False)

    # Sub-state: "pickup" | "dropoff" | None  (not part of public RobotState)
    _operation_type: Optional[str] = field(default=None, repr=False)

    # Destination when navigating to charger (pre-computed)
    _charger_target: Optional[Tuple[int, int]] = field(default=None, repr=False)

    # Flag: needs replanning this tick
    _needs_replan: bool = field(default=False, repr=False)

    # Metrics: total replans triggered for this robot
    _replan_count: int = field(default=0, repr=False)

    @property
    def position(self) -> Tuple[int, int]:
        return (self.x, self.y)

    @position.setter
    def position(self, pos: Tuple[int, int]) -> None:
        self.x, self.y = int(pos[0]), int(pos[1])

    @property
    def wait_ticks_so_far(self) -> int:
        return self._wait_ticks

    @wait_ticks_so_far.setter
    def wait_ticks_so_far(self, val: int) -> None:
        self._wait_ticks = int(val)

    @property
    def is_idle(self) -> bool:
        return self.state == RobotState.IDLE

    @property
    def is_charging(self) -> bool:
        return self.state == RobotState.CHARGING

    @property
    def needs_charge(self) -> bool:
        return self.battery_pct < 20.0

    def clamp_battery(self) -> None:
        self.battery_pct = max(0.0, min(100.0, self.battery_pct))

    def distance_to_goal(self) -> int:
        """Manhattan distance to the next planned path destination."""
        if not self.path:
            return 0
        last = self.path[-1]
        return abs(last.x - self.x) + abs(last.y - self.y)
