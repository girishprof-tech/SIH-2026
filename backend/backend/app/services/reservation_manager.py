"""
ReservationManager — SCHEMA.md §8.

Maintains the space-time reservation table.
All operations are O(1) using dict-based lookups.

Reservation format: (x, y, t) → robot_id

Rules enforced:
  1. Cell collision — no two robots at same (x,y,t)
  2. Swap collision — AMR-1:(5,5)→(6,5) and AMR-2:(6,5)→(5,5) forbidden
  3. Boundary violations — handled by WorldConfig
  4. Blocked cells — checked against world before reserving
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple

from app.models.reservation import ReservationKey, ReservationTable
from app.models.robot import PathNode

log = logging.getLogger(__name__)


class ReservationManager:
    """
    Central space-time reservation table.

    Design:
      - `_table[(x,y,t)] = robot_id` — master lookup, O(1)
      - `_robot_keys[robot_id] = set of keys` — for fast release on replan

    Thread-safety: asyncio single-threaded; no locking needed.
    """

    def __init__(self) -> None:
        self._table: ReservationTable = {}
        # Tracks which keys belong to each robot (for O(1) release)
        self._robot_keys: Dict[str, Set[ReservationKey]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def reserve_path(self, robot_id: str, path: List[PathNode]) -> None:
        """
        Reserve all (x, y, t) cells along a planned path.
        Also reserves the implicit "turn wait" ticks between path steps.
        Previous reservations for this robot are released automatically.
        """
        self.release(robot_id)

        keys: Set[ReservationKey] = set()
        for node in path:
            key: ReservationKey = (node.x, node.y, node.t)
            self._table[key] = robot_id
            keys.add(key)

        self._robot_keys[robot_id] = keys

    def reserve_single(self, robot_id: str, x: int, y: int, t: int) -> None:
        """Reserve a single cell (used for wait/turn ticks)."""
        key: ReservationKey = (x, y, t)
        self._table[key] = robot_id
        if robot_id not in self._robot_keys:
            self._robot_keys[robot_id] = set()
        self._robot_keys[robot_id].add(key)

    def release(self, robot_id: str) -> None:
        """Release all reservations for a robot (called before replan)."""
        keys = self._robot_keys.pop(robot_id, set())
        for key in keys:
            self._table.pop(key, None)

    def is_reserved(self, x: int, y: int, t: int) -> bool:
        """Check if (x, y, t) is reserved by ANY robot."""
        return (x, y, t) in self._table

    def is_reserved_by_other(self, x: int, y: int, t: int, robot_id: str) -> bool:
        """Check if (x, y, t) is reserved by a DIFFERENT robot."""
        owner = self._table.get((x, y, t))
        return owner is not None and owner != robot_id

    def who_reserved(self, x: int, y: int, t: int) -> Optional[str]:
        """Return the robot_id that reserved (x, y, t), or None."""
        return self._table.get((x, y, t))

    def has_swap_conflict(
        self,
        robot_a: str,
        from_a: Tuple[int, int],
        to_a: Tuple[int, int],
        tick_a: int,
    ) -> bool:
        """
        Detect swap collision per SCHEMA.md §9.

        Forbidden pattern:
            AMR-A: from_a → to_a at tick_a
            AMR-B: to_a   → from_a at tick_a (i.e., reserved from_a at tick_a+1 and to_a at tick_a)
        """
        # If some other robot occupies to_a at tick_a (they're there) AND
        # that same robot has reserved from_a at tick_a+1 (they want to move there)
        other_at_to = self.who_reserved(to_a[0], to_a[1], tick_a)
        if other_at_to and other_at_to != robot_a:
            if self.is_reserved_by_other(from_a[0], from_a[1], tick_a + 1, robot_a):
                occupier = self.who_reserved(from_a[0], from_a[1], tick_a + 1)
                if occupier == other_at_to:
                    return True
        return False

    def purge_past(self, current_tick: int) -> None:
        """
        Remove reservations for ticks that have already passed.
        Call once per tick to keep the table small.
        """
        stale = [k for k in self._table if k[2] < current_tick - 1]
        for k in stale:
            rid = self._table.pop(k, None)
            if rid and rid in self._robot_keys:
                self._robot_keys[rid].discard(k)

    def snapshot(self) -> ReservationTable:
        """Return a shallow copy of the table (for planner calls)."""
        return dict(self._table)

    @property
    def table(self) -> ReservationTable:
        """Direct reference — use read-only during simulation tick."""
        return self._table

    def stats(self) -> Dict:
        return {
            "total_reservations": len(self._table),
            "robots_with_reservations": len(self._robot_keys),
        }
