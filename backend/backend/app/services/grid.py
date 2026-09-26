"""
grid.py — Static warehouse map for SIH26123 (Edge-AI Fleet Coordination).
Owner: Member 2 — Core Algorithm Engineer.

Wraps the "Obstacles / Static Map" contract in SCHEMA.md Section 9 and the
world constants in Section 1. Responsibilities:

  * bounds checking (grid is 0..width-1 x 0..height-1, default 30x30)
  * O(1) obstacle lookup via a NumPy boolean mask
  * a NetworkX graph of free (non-obstacle) cells, used to compute a true
    obstacle-aware shortest-path heuristic for Space-Time A* (pathfinder.py)

This module intentionally knows nothing about time, robots, or reservations —
that all lives in pathfinder.py / reservations.py. Keeping the static map
separate means Member 1's obstacle layout can be swapped in/out without
touching the search algorithm.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Set, Tuple

import networkx as nx
import numpy as np

Position = Tuple[int, int]

GRID_WIDTH = 30
GRID_HEIGHT = 30


class WarehouseGrid:
    """Static, obstacle-aware representation of the warehouse floor."""

    def __init__(
        self,
        obstacles: Iterable[Position] = (),
        width: int = GRID_WIDTH,
        height: int = GRID_HEIGHT,
    ) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("Grid width/height must be positive.")

        self.width = width
        self.height = height
        self.obstacles: Set[Position] = set()

        self._blocked = np.zeros((width, height), dtype=bool)
        for raw in obstacles:
            pos = (int(raw[0]), int(raw[1]))
            if not self.in_bounds(pos):
                raise ValueError(
                    f"Obstacle {pos} lies outside the {width}x{height} grid."
                )
            self.obstacles.add(pos)
            self._blocked[pos[0], pos[1]] = True

        self._graph = self._build_graph()
        # Cache of {goal: {node: distance}} built lazily via single-source BFS.
        self._dist_cache: Dict[Position, Dict[Position, int]] = {}
        # Pod-slot physical occupancy tracking: (x, y) -> robot_id (Phase 1.5 Fix 1)
        self.pod_slot_occupants: Dict[Position, str] = {}

    def set_pod_slot_occupant(self, pos: Position, robot_id: Optional[str]) -> None:
        """Mark a pod slot cell as occupied by robot_id during lifting/carrying, or cleared (None)."""
        int_pos = (int(pos[0]), int(pos[1]))
        if robot_id is None:
            self.pod_slot_occupants.pop(int_pos, None)
        else:
            self.pod_slot_occupants[int_pos] = robot_id

    def is_pod_slot_occupied_by_other(self, pos: Position, robot_id: Optional[str]) -> bool:
        """Returns True if the cell is an occupied pod slot claimed by another robot."""
        occupant = self.pod_slot_occupants.get((int(pos[0]), int(pos[1])))
        return occupant is not None and occupant != robot_id

    # -- construction helpers ------------------------------------------------

    @classmethod
    def from_schema_dict(
        cls,
        static_map: dict,
        width: int = GRID_WIDTH,
        height: int = GRID_HEIGHT,
    ) -> "WarehouseGrid":
        """
        Build a grid directly from the JSON shape in SCHEMA.md Section 9, e.g.:

            {
              "obstacles": [{"x": 5, "y": 5}, {"x": 5, "y": 6}],
              "charging_stations": [{"x": 0, "y": 0}],
              "pickup_stations": [{"x": 4, "y": 22}]
            }

        Only "obstacles" affects pathfinding; charging/pickup stations are
        ordinary walkable cells and are not passed to the constructor.
        """
        obstacles = [(o["x"], o["y"]) for o in static_map.get("obstacles", [])]
        return cls(obstacles=obstacles, width=width, height=height)

    # -- queries ---------------------------------------------------------

    def in_bounds(self, pos: Position) -> bool:
        x, y = pos
        return 0 <= x < self.width and 0 <= y < self.height

    def is_obstacle(self, pos: Position) -> bool:
        x, y = pos
        return bool(self._blocked[x, y])

    def is_free(self, pos: Position) -> bool:
        return self.in_bounds(pos) and not self.is_obstacle(pos)

    # -- graph / heuristic -------------------------------------------------

    def _build_graph(self) -> nx.Graph:
        g = nx.grid_2d_graph(self.width, self.height)  # 4-connected, no diagonals
        g.remove_nodes_from(self.obstacles)
        return g

    def true_distance(self, a: Position, b: Position) -> Optional[int]:
        """
        Obstacle-aware shortest-path distance from `a` to `b` on the static
        grid (ignores time, other robots, and turn cost). This is admissible
        for Space-Time A*: turning only ever adds extra ticks on top of pure
        movement, so this never overestimates the true cost.

        Distances from a single goal are computed once via BFS and cached,
        so repeated calls with the same goal (typical: many robots pathing to
        the same handful of pickup/dropoff points) are O(1) after the first.

        Returns None if `b` is an obstacle/out of bounds, or `a` cannot reach
        `b` at all (e.g. sealed off by obstacles).
        """
        if b not in self._graph:
            return None
        if b not in self._dist_cache:
            self._dist_cache[b] = nx.single_source_shortest_path_length(self._graph, b)
        return self._dist_cache[b].get(a)

    def clear_heuristic_cache(self) -> None:
        """Call this if obstacles are mutated after construction (not expected
        mid-simulation per SCHEMA.md, but useful for tooling/tests)."""
        self._dist_cache.clear()
