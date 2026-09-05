"""
Dynamic Obstacle model — SCHEMA.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class TemporaryObstacle:
    """
    A time-limited blocked cell.

    Fields match SCHEMA.md §3 exactly.
    Lifecycle: appears at created_tick, expires at expires_at_tick (exclusive).
    """

    obstacle_id: str
    x: int
    y: int
    created_tick: int
    expires_at_tick: int

    @property
    def position(self) -> Tuple[int, int]:
        return (self.x, self.y)

    def is_active(self, current_tick: int) -> bool:
        """True when the obstacle blocks the cell at this tick."""
        return self.created_tick <= current_tick < self.expires_at_tick
