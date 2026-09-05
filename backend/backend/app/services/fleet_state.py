"""
FleetState — Authoritative in-memory simulation state.

This is the SINGLE SOURCE OF TRUTH for:
  - current tick
  - all robot states
  - all tasks
  - temporary obstacles
  - reservation table
  - active conflicts
  - warehouse config

NEVER:
  - query a database in this hot-path
  - copy the entire fleet unnecessarily
  - let WebSocket clients modify this state directly

All mutations happen through SimulationEngine.tick().
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from app.core.config import get_settings
from app.models.obstacle import TemporaryObstacle
from app.models.robot import Heading, PathNode, Robot, RobotState
from app.models.task import Task, TaskStatus
from app.models.world import WorldConfig, build_default_world
from app.services.conflict_manager import ConflictRecord
from app.services.reservation_manager import ReservationManager

log = logging.getLogger(__name__)

cfg = get_settings()


class FleetState:
    """
    Authoritative simulation state container.

    Initialized once at startup.
    Reset via reset() for SCHEMA.md RESET control.
    """

    def __init__(self) -> None:
        self.world: WorldConfig = build_default_world(cfg.GRID_WIDTH, cfg.GRID_HEIGHT)
        self.tick: int = 0
        self.timestamp_ms: int = 0
        self.is_running: bool = False

        # Core state maps (O(1) access by ID)
        self.robots: Dict[str, Robot] = {}
        self.tasks: Dict[str, Task] = {}
        self.temp_obstacles: Dict[str, TemporaryObstacle] = {}

        # Active conflict records (refreshed every tick by ConflictManager)
        self.active_conflicts: List[ConflictRecord] = []

        # Metrics snapshot (updated by telemetry service)
        self.last_tick_processing_ms: float = 0.0
        self.planner_latency_ms: float = 0.0
        self.broadcast_latency_ms: float = 0.0

        # Pending external events (thread-safe append from REST handlers)
        self._pending_tasks: List[Task] = []
        self._pending_obstacles: List[TemporaryObstacle] = []
        self._obstacle_removals: List[str] = []

        self._initialize_fleet()

    # ── Initialization ────────────────────────────────────────────────────────

    def _initialize_fleet(self) -> None:
        """
        Spawn AMR-01 … AMR-{FLEET_SIZE} at deterministic start positions.
        Positions are spread around the warehouse border to avoid initial collisions.
        """
        self.robots.clear()
        self.tasks.clear()
        self.temp_obstacles.clear()
        self.active_conflicts.clear()
        self._pending_tasks.clear()
        self._pending_obstacles.clear()
        self._obstacle_removals.clear()

        # Spread robots along bottom row away from obstacles/chargers
        start_positions = [
            (1, 28), (3, 28), (5, 28), (7, 28), (9, 28),
            (11, 28), (13, 28), (15, 28), (17, 28), (19, 28),
        ]
        # Extend if fleet_size > 10
        for i in range(10, cfg.FLEET_SIZE):
            start_positions.append((21 + (i - 10) * 2, 28))

        for i in range(cfg.FLEET_SIZE):
            robot_id = f"{cfg.ROBOT_PREFIX}-{i + 1:02d}"
            sx, sy = start_positions[i % len(start_positions)]
            # Ensure start position is not on a static obstacle
            while self.world.is_static_blocked(sx, sy):
                sy -= 1

            robot = Robot(
                robot_id=robot_id,
                x=sx,
                y=sy,
                heading=Heading.NORTH,
                state=RobotState.IDLE,
                battery_pct=100.0 - i * 5.0,   # Stagger battery for demo
                current_task_id=None,
                priority_score=0,
                last_updated_tick=0,
            )
            self.robots[robot_id] = robot
            log.debug("Spawned %s at (%d,%d) battery=%.0f%%", robot_id, sx, sy, robot.battery_pct)

    # ── Obstacle management ───────────────────────────────────────────────────

    def get_active_temp_blocked(self) -> Set[Tuple[int, int]]:
        """Return set of currently blocked temporary obstacle positions."""
        return {
            obs.position
            for obs in self.temp_obstacles.values()
            if obs.is_active(self.tick)
        }

    def add_temp_obstacle(self, obs: TemporaryObstacle) -> None:
        """Queue a new temporary obstacle (processed at tick boundary)."""
        self._pending_obstacles.append(obs)

    def remove_temp_obstacle(self, obstacle_id: str) -> None:
        """Queue removal of a temporary obstacle."""
        self._obstacle_removals.append(obstacle_id)

    def flush_obstacle_events(self) -> None:
        """Apply queued obstacle additions and removals at tick start."""
        for obs in self._pending_obstacles:
            self.temp_obstacles[obs.obstacle_id] = obs
            log.info("OBSTACLE_ADDED id=%s pos=(%d,%d)", obs.obstacle_id, obs.x, obs.y)
        self._pending_obstacles.clear()

        for oid in self._obstacle_removals:
            removed = self.temp_obstacles.pop(oid, None)
            if removed:
                log.info("OBSTACLE_REMOVED id=%s", oid)
        self._obstacle_removals.clear()

    def expire_obstacles(self) -> None:
        """Remove obstacles whose expires_at_tick has been reached."""
        expired = [
            oid for oid, obs in self.temp_obstacles.items()
            if self.tick >= obs.expires_at_tick
        ]
        for oid in expired:
            log.info("OBSTACLE_EXPIRED id=%s tick=%d", oid, self.tick)
            del self.temp_obstacles[oid]

    # ── Pending task queue ────────────────────────────────────────────────────

    def queue_task(self, task: Task) -> None:
        """REST handler pushes tasks here; engine drains each tick."""
        self._pending_tasks.append(task)
        self.tasks[task.task_id] = task

    def flush_task_events(self) -> List[Task]:
        """Return and clear the pending task queue."""
        tasks = list(self._pending_tasks)
        self._pending_tasks.clear()
        return tasks

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Full simulation reset — SCHEMA.md §18."""
        log.info("SIMULATION_RESET tick=%d", self.tick)
        self.tick = 0
        self.timestamp_ms = 0
        self.is_running = False
        self.last_tick_processing_ms = 0.0
        self._initialize_fleet()

    # ── Serialization helpers (called by WebSocket broadcaster) ───────────────

    def robots_as_dicts(self) -> List[dict]:
        """
        Serialize all robots to SCHEMA.md §16 / §4 format.
        Called once per tick, result shared with all WS clients.
        """
        result = []
        for r in self.robots.values():
            result.append({
                "robot_id": r.robot_id,
                "position": {"x": r.x, "y": r.y},
                "heading": r.heading.value,
                "state": r.state.value,
                "battery_pct": round(r.battery_pct, 2),
                "current_task_id": r.current_task_id,
                "priority_score": r.priority_score,
                "last_updated_tick": r.last_updated_tick,
                "path": [{"x": n.x, "y": n.y, "t": n.t} for n in r.path[:10]],
            })
        return result

    def obstacles_as_dicts(self) -> List[dict]:
        """Serialize active temporary obstacles for WebSocket."""
        return [
            {
                "obstacle_id": obs.obstacle_id,
                "position": {"x": obs.x, "y": obs.y},
                "created_tick": obs.created_tick,
                "expires_at_tick": obs.expires_at_tick,
            }
            for obs in self.temp_obstacles.values()
            if obs.is_active(self.tick)
        ]

    def conflicts_as_dicts(self) -> List[dict]:
        """Serialize active conflicts for WebSocket."""
        return [
            {
                "robot_ids": c.robot_ids,
                "cell": {"x": c.cell[0], "y": c.cell[1]},
                "resolved_by": c.resolved_by,
            }
            for c in self.active_conflicts
        ]
