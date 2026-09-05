"""
ConflictManager — SCHEMA.md §13.

Detects and resolves robot conflicts using the priority formula from SCHEMA.md.

Priority Formula:
    Priority = (Urgency × 100)
             + (Battery < 20 ? 500 : 0)
             + (WaitTicks × 10)
             - DistanceToGoal

Tie-breaker: lower robot_id wins.

Resolution Flow:
    Conflict → Calculate Scores → Higher Score Wins
    → Loser Waits 1 Tick → Recheck → Replan if Required

Conflict Triggers (SCHEMA.md §13):
    1. Robots within 2-cell radius
    2. Future reservation overlap
    3. Swap rule violation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from app.models.robot import Robot, RobotState
from app.models.task import Task
from app.services.reservation_manager import ReservationManager

log = logging.getLogger(__name__)


@dataclass
class ConflictRecord:
    """A detected conflict between robots."""
    robot_ids: List[str]
    cell: Tuple[int, int]
    resolved_by: Optional[str] = None  # e.g. "AMR-03_yield"


class ConflictManager:
    """
    Detects conflicts and applies SCHEMA.md §13 priority resolution.

    Designed to be replaceable — Member 3 (Conflict Resolution) can swap this
    with a more sophisticated engine by implementing the same interface.
    """

    def __init__(
        self,
        reservation_manager: ReservationManager,
        conflict_radius: int = 2,
    ) -> None:
        self._reservations = reservation_manager
        self._conflict_radius = conflict_radius
        self._active_conflicts: List[ConflictRecord] = []

    # ── Public API ────────────────────────────────────────────────────────────

    def detect_and_resolve(
        self,
        robots: Dict[str, Robot],
        tasks: Dict[str, Task],
        current_tick: int,
    ) -> List[ConflictRecord]:
        """
        Main entry point — called once per tick.

        1. Detect conflicts (radius + reservation overlap + swap).
        2. Score each conflicting pair.
        3. Yield lower-priority robot for 1 tick.
        4. Mark yielders as needing replan.

        Returns the list of active conflicts for WebSocket broadcast.
        """
        self._active_conflicts = []

        robot_list = list(robots.values())
        n = len(robot_list)

        for i in range(n):
            for j in range(i + 1, n):
                ra = robot_list[i]
                rb = robot_list[j]

                if not self._is_nearby(ra, rb):
                    continue

                conflict_cell = self._find_conflict_cell(ra, rb, current_tick)
                if conflict_cell is None:
                    continue

                # Score both robots
                task_a = tasks.get(ra.current_task_id or "")
                task_b = tasks.get(rb.current_task_id or "")

                score_a = self._priority_score(ra, task_a)
                score_b = self._priority_score(rb, task_b)

                # Determine winner and loser
                winner, loser = self._resolve_tie(ra, rb, score_a, score_b)

                rec = ConflictRecord(
                    robot_ids=[ra.robot_id, rb.robot_id],
                    cell=conflict_cell,
                    resolved_by=f"{loser.robot_id}_yield",
                )
                self._active_conflicts.append(rec)

                # Yield: loser waits this tick, marks replan needed
                self._yield_robot(loser, current_tick)

                log.debug(
                    "CONFLICT_DETECTED cell=%s winner=%s loser=%s scores=(%d,%d)",
                    conflict_cell, winner.robot_id, loser.robot_id, score_a, score_b,
                )

        return self._active_conflicts

    @property
    def active_conflicts(self) -> List[ConflictRecord]:
        return self._active_conflicts

    # ── Priority Scoring (SCHEMA.md §13) ─────────────────────────────────────

    @staticmethod
    def _priority_score(robot: Robot, task: Optional[Task]) -> int:
        urgency = task.urgency if task else 1
        battery_bonus = 500 if robot.battery_pct < 20 else 0
        wait_bonus = robot._wait_ticks * 10
        dist = robot.distance_to_goal()
        return (urgency * 100) + battery_bonus + wait_bonus - dist

    @staticmethod
    def _resolve_tie(
        ra: Robot, rb: Robot, score_a: int, score_b: int
    ) -> Tuple[Robot, Robot]:
        """Returns (winner, loser). Lower robot_id wins on tie."""
        if score_a > score_b:
            return ra, rb
        if score_b > score_a:
            return rb, ra
        # Tie-breaker: lower robot_id wins
        if ra.robot_id < rb.robot_id:
            return ra, rb
        return rb, ra

    # ── Conflict Detection ────────────────────────────────────────────────────

    def _is_nearby(self, ra: Robot, rb: Robot) -> bool:
        return (
            abs(ra.x - rb.x) + abs(ra.y - rb.y) <= self._conflict_radius
        )

    def _find_conflict_cell(
        self, ra: Robot, rb: Robot, current_tick: int
    ) -> Optional[Tuple[int, int]]:
        """
        Returns the conflict cell if any conflict is found, else None.

        Checks:
        1. Same cell collision
        2. Swap collision (via reservation manager)
        3. Future reservation overlap within 3 ticks
        """
        # 1. Same cell
        if (ra.x, ra.y) == (rb.x, rb.y):
            return (ra.x, ra.y)

        # 2. Swap collision: check if they're about to trade cells
        #    (simplified: check if each robot's path leads into the other's cell next tick)
        ra_next = self._next_cell(ra)
        rb_next = self._next_cell(rb)
        if ra_next and rb_next:
            if ra_next == (rb.x, rb.y) and rb_next == (ra.x, ra.y):
                return ra_next

        # 3. Future reservation overlap (next 3 ticks)
        for dt in range(1, 4):
            t = current_tick + dt
            owner_ra_next = None
            if ra_next:
                owner_ra_next = self._reservations.who_reserved(ra_next[0], ra_next[1], t)
            if owner_ra_next == rb.robot_id:
                return ra_next

        return None

    @staticmethod
    def _next_cell(robot: Robot) -> Optional[Tuple[int, int]]:
        """Return robot's next planned position, or None."""
        if robot.path and robot._path_idx < len(robot.path):
            node = robot.path[robot._path_idx]
            return (node.x, node.y)
        return None

    # ── Yield Behavior ────────────────────────────────────────────────────────

    @staticmethod
    def _yield_robot(robot: Robot, current_tick: int) -> None:
        """
        Make the losing robot wait this tick and flag for replan.
        Does not move the robot — simulation engine handles that.
        """
        if robot.state not in (RobotState.CHARGING, RobotState.EMERGENCY_STOP):
            robot.state = RobotState.CONFLICT_NEGOTIATING
            robot._wait_ticks += 1
            robot._needs_replan = True
            log.debug("CONFLICT_YIELD robot=%s tick=%d", robot.robot_id, current_tick)
