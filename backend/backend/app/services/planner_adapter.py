"""
PlannerAdapter — SCHEMA.md §14.

This is the integration point for Member 2 (Pathfinding).

CONTRACT:
    find_path(start, goal, current_tick, reservation_table) -> list[{x, y, t}]

IMPLEMENTATIONS:
    MockPlannerAdapter  — simple BFS/A* stub for local development and testing
    ExternalPlannerAdapter — delegates to an external HTTP/IPC pathfinder

HOW TO PLUG IN:
    1. Implement AbstractPlannerAdapter.
    2. Register it in get_planner_adapter() below.
    3. Set PLANNER_BACKEND env var.
"""

from __future__ import annotations

import abc
import heapq
import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from app.core.config import get_settings
from app.models.reservation import ReservationTable
from app.models.world import WorldConfig

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Interface
# ─────────────────────────────────────────────────────────────────────────────

class AbstractPlannerAdapter(abc.ABC):
    """
    The pathfinding contract (SCHEMA.md §14).

    Any implementation must:
      - Avoid blocked cells (static + temporary obstacles).
      - Respect the reservation table to prevent space-time collisions.
      - Prevent swap collisions.
      - Return an empty list if no path exists.
    """

    @abc.abstractmethod
    def find_path(
        self,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        current_tick: int,
        reservation_table: ReservationTable,
        world: WorldConfig,
        temp_blocked: Set[Tuple[int, int]],
    ) -> List[Dict]:
        """
        Returns a list of {"x": int, "y": int, "t": int} waypoints.
        The first node is the robot's position at current_tick.
        """

    @property
    def name(self) -> str:
        return self.__class__.__name__


# ─────────────────────────────────────────────────────────────────────────────
# Mock / built-in planner (Space-Time A*)
# ─────────────────────────────────────────────────────────────────────────────

class MockPlannerAdapter(AbstractPlannerAdapter):
    """
    Space-Time A* planner.

    This is the built-in fallback used when no external planner is connected.
    It satisfies the SCHEMA.md contract exactly.

    Complexity: O(T * W * H * log(T * W * H)) where T is search horizon.
    For 30×30 warehouse with 10 robots this is very fast (< 1 ms per plan).
    """

    MAX_TIME_HORIZON = 120  # Ticks — prevents infinite search

    def find_path(
        self,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        current_tick: int,
        reservation_table: ReservationTable,
        world: WorldConfig,
        temp_blocked: Set[Tuple[int, int]],
    ) -> List[Dict]:
        t0 = time.monotonic()

        if start == goal:
            return [{"x": start[0], "y": start[1], "t": current_tick}]

        # Combined blocked set (static + temporary)
        blocked: Set[Tuple[int, int]] = world.static_obstacles | temp_blocked

        # State: (f, g, x, y, t, parent_idx)
        # We store expanded nodes in a list to reconstruct path
        open_heap: list = []
        # (f_score, g, x, y, t)
        h0 = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
        heapq.heappush(open_heap, (h0, 0, start[0], start[1], current_tick))

        # visited[(x, y, t)] = (g, parent_key)
        visited: Dict[Tuple[int, int, int], Tuple[int, Optional[Tuple[int, int, int]]]] = {}
        visited[(start[0], start[1], current_tick)] = (0, None)

        DIRS = [(0, -1), (0, 1), (1, 0), (-1, 0)]  # N, S, E, W

        while open_heap:
            f, g, x, y, t = heapq.heappop(open_heap)

            if (x, y) == goal:
                # Reconstruct path
                path: List[Dict] = []
                key: Optional[Tuple[int, int, int]] = (x, y, t)
                while key is not None:
                    path.append({"x": key[0], "y": key[1], "t": key[2]})
                    _, parent = visited[key]
                    key = parent
                path.reverse()
                elapsed_ms = (time.monotonic() - t0) * 1000
                log.debug("Planner found path in %.2f ms (%d nodes)", elapsed_ms, len(path))
                return path

            # Prune search horizon
            if t - current_tick >= self.MAX_TIME_HORIZON:
                continue

            nt = t + 1

            # Option 1: Move in each direction
            for dx, dy in DIRS:
                nx, ny = x + dx, y + dy
                if not world.in_bounds(nx, ny):
                    continue
                if (nx, ny) in blocked:
                    continue
                # Reservation check: target cell at nt
                if reservation_table.get((nx, ny, nt)) is not None:
                    continue
                # Swap collision: if another robot moves FROM (nx,ny) TO (x,y) at nt
                if reservation_table.get((x, y, nt)) is not None and \
                   reservation_table.get((nx, ny, t)) is not None:
                    continue

                key = (nx, ny, nt)
                ng = g + 1
                if key in visited and visited[key][0] <= ng:
                    continue
                h = abs(nx - goal[0]) + abs(ny - goal[1])
                visited[key] = (ng, (x, y, t))
                heapq.heappush(open_heap, (ng + h, ng, nx, ny, nt))

            # Option 2: Wait in place (costs 1 tick)
            wait_key = (x, y, nt)
            if reservation_table.get((x, y, nt)) is None:
                wg = g + 1
                if wait_key not in visited or visited[wait_key][0] > wg:
                    h = abs(x - goal[0]) + abs(y - goal[1])
                    visited[wait_key] = (wg, (x, y, t))
                    heapq.heappush(open_heap, (wg + h, wg, x, y, nt))

        log.warning("Planner: no path from %s to %s", start, goal)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# External planner stub (plug-in point for Member 2)
# ─────────────────────────────────────────────────────────────────────────────

class ExternalPlannerAdapter(AbstractPlannerAdapter):
    """
    Delegates path-finding to an external service (HTTP / IPC).

    Member 2 can run their planner as a local subprocess or HTTP server.
    Set PLANNER_BACKEND=external and PLANNER_URL=http://localhost:7000 in .env.

    Expected POST /find_path:
        {
          "start": {"x": ..., "y": ...},
          "goal": {"x": ..., "y": ...},
          "current_tick": ...,
          "reservation_table": [[x, y, t], ...]
        }

    Expected response:
        {"path": [{"x":..., "y":..., "t":...}, ...]}
    """

    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/") + "/find_path"
        try:
            import httpx
            self._client = httpx.Client(timeout=0.1)  # 100ms max — hot path
        except ImportError:
            log.warning("httpx not installed — ExternalPlannerAdapter will always fall back")
            self._client = None  # type: ignore[assignment]

    def find_path(
        self,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        current_tick: int,
        reservation_table: ReservationTable,
        world: WorldConfig,
        temp_blocked: Set[Tuple[int, int]],
    ) -> List[Dict]:
        if self._client is None:
            log.error("ExternalPlannerAdapter: no httpx client")
            return []
        try:
            resp = self._client.post(self._url, json={
                "start": {"x": start[0], "y": start[1]},
                "goal": {"x": goal[0], "y": goal[1]},
                "current_tick": current_tick,
                "reservation_table": [list(k) for k in reservation_table],
            })
            resp.raise_for_status()
            return resp.json().get("path", [])
        except Exception as exc:
            log.error("ExternalPlannerAdapter error: %s — falling back to mock", exc)
            # Graceful fallback to built-in planner
            return MockPlannerAdapter().find_path(
                start, goal, current_tick, reservation_table, world, temp_blocked
            )


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

_planner_instance: Optional[AbstractPlannerAdapter] = None


def get_planner_adapter() -> AbstractPlannerAdapter:
    """Return the configured planner adapter (singleton)."""
    global _planner_instance
    if _planner_instance is not None:
        return _planner_instance

    cfg = get_settings()
    if cfg.PLANNER_BACKEND == "external" and cfg.PLANNER_URL:
        log.info("Using ExternalPlannerAdapter → %s", cfg.PLANNER_URL)
        _planner_instance = ExternalPlannerAdapter(cfg.PLANNER_URL)
    else:
        log.info("Using MockPlannerAdapter (built-in Space-Time A*)")
        _planner_instance = MockPlannerAdapter()

    return _planner_instance


def reset_planner_adapter() -> None:
    """For testing: reset the singleton."""
    global _planner_instance
    _planner_instance = None
