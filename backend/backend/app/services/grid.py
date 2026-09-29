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

from collections import deque
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

import networkx as nx
import numpy as np

Position = Tuple[int, int]

GRID_WIDTH = 30
GRID_HEIGHT = 30

# Kinematic time-window buffer defaults (ticks)
DEFAULT_VACANCY_DELTA = 1  # Minimum ticks a cell must stay vacant after a robot leaves
HEAVY_PAYLOAD_DELTA = 2   # Buffer for robots carrying > 30 kg


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

        # Shelf / pod-slot cell set (populated via register_shelf_cells)
        self._shelf_cells: Set[Position] = set()
        # Narrow corridor segments: maps each corridor cell → its full corridor frozenset
        self._corridor_map: Dict[Position, FrozenSet[Position]] = {}
        # All corridor segments as a list of frozensets
        self._corridor_segments: List[FrozenSet[Position]] = []

    # -- shelf cell registration (G2P Part 2) --------------------------------

    def register_shelf_cells(self, shelf_positions: Iterable[Position]) -> None:
        """Register all pod-slot / shelf rack cells as impassable-by-default.
        Called once at startup with world.pod_slots.values()."""
        self._shelf_cells = {(int(p[0]), int(p[1])) for p in shelf_positions}

    def is_shelf_cell(self, pos: Position) -> bool:
        """Returns True if pos is a storage shelf / pod-slot cell."""
        return (int(pos[0]), int(pos[1])) in self._shelf_cells

    def is_free_for_robot(
        self,
        pos: Position,
        robot_id: Optional[str] = None,
        target_shelf_pos: Optional[Position] = None,
        carrying_pod: bool = False,
    ) -> bool:
        """G2P-aware traversability:
        - Shelf cells are IMPASSABLE by default.
        - Unladen exception: exactly the target_shelf_pos is traversable.
        - Laden robots (carrying_pod=True): ALL shelf cells are impassable.
        - Normal obstacles are always impassable.
        """
        if not self.in_bounds(pos):
            return False
        if self.is_obstacle(pos):
            return False
        if self.is_shelf_cell(pos):
            if carrying_pod:
                return False  # Laden robots never enter shelf cells
            if target_shelf_pos is not None:
                int_pos = (int(pos[0]), int(pos[1]))
                int_target = (int(target_shelf_pos[0]), int(target_shelf_pos[1]))
                return int_pos == int_target  # Only the assigned shelf is enterable
            return False  # Shelf cells are impassable by default
        if self.is_pod_slot_occupied_by_other(pos, robot_id):
            return False
        return True

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

    # -- Corridor Segment Detection (Part 1A) --------------------------------

    def _free_neighbors(self, pos: Position) -> List[Position]:
        """Return 4-connected free neighbors of pos."""
        x, y = pos
        out = []
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nx_, ny_ = x + dx, y + dy
            if self.in_bounds((nx_, ny_)) and self.is_free((nx_, ny_)):
                out.append((nx_, ny_))
        return out

    def _is_narrow_cell(self, pos: Position) -> bool:
        """A cell is 'narrow' if it has exactly 2 free neighbors that are
        collinear (both along the same axis). This identifies interior cells
        of 1-cell-wide corridors."""
        if not self.is_free(pos):
            return False
        neighbors = self._free_neighbors(pos)
        if len(neighbors) != 2:
            return False
        (x1, y1), (x2, y2) = neighbors
        # Collinear check: both neighbors on same horizontal or vertical line
        return (x1 == x2) or (y1 == y2)

    def compute_corridor_segments(self) -> None:
        """Identify all contiguous 1-cell-wide corridor stretches bounded
        by junctions (cells with >2 free neighbors) or dead ends.
        Store results in self._corridor_map and self._corridor_segments."""
        visited: Set[Position] = set()
        self._corridor_map.clear()
        self._corridor_segments.clear()

        for x in range(self.width):
            for y in range(self.height):
                pos = (x, y)
                if pos in visited or not self._is_narrow_cell(pos):
                    continue
                # BFS/flood-fill to find the entire narrow corridor segment
                segment: Set[Position] = set()
                queue = deque([pos])
                while queue:
                    cell = queue.popleft()
                    if cell in segment:
                        continue
                    if not self._is_narrow_cell(cell):
                        continue
                    segment.add(cell)
                    visited.add(cell)
                    for nb in self._free_neighbors(cell):
                        if nb not in segment:
                            queue.append(nb)

                if len(segment) >= 2:  # Only meaningful corridors (2+ narrow cells)
                    frozen = frozenset(segment)
                    self._corridor_segments.append(frozen)
                    for cell in segment:
                        self._corridor_map[cell] = frozen

    def get_corridor_segment(self, pos: Position) -> Optional[FrozenSet[Position]]:
        """If pos is inside a narrow corridor, return the full corridor segment frozenset.
        Returns None if pos is a junction or open area."""
        return self._corridor_map.get((int(pos[0]), int(pos[1])))

    def corridor_entry_cells(self, segment: FrozenSet[Position]) -> Set[Position]:
        """Return the junction/turnout cells adjacent to a corridor segment
        (the cells a robot must occupy before entering the corridor)."""
        entries: Set[Position] = set()
        for cell in segment:
            for nb in self._free_neighbors(cell):
                if nb not in segment:
                    entries.add(nb)
        return entries

    # -- Kinematic buffer computation ----------------------------------------

    @staticmethod
    def compute_vacancy_delta(payload_weight_kg: float = 0.0) -> int:
        """Compute the kinematic time-window buffer (in ticks) based on payload."""
        if payload_weight_kg > 30.0:
            return HEAVY_PAYLOAD_DELTA
        return DEFAULT_VACANCY_DELTA
