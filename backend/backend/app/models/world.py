"""
World geometry model.

Holds all static warehouse data pre-computed once at startup.
NEVER re-derived inside the simulation hot-path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Set, Tuple


@dataclass
class WorldConfig:
    """
    Static snapshot of the warehouse. Computed once, never mutated.
    Coordinate system: origin (0,0) top-left, +x → East, +y → South.
    """

    width: int
    height: int
    cell_size_m: float

    # Precomputed immutable sets for O(1) lookup
    static_obstacles: FrozenSet[Tuple[int, int]]
    charging_stations: FrozenSet[Tuple[int, int]]
    pickup_stations: FrozenSet[Tuple[int, int]]
    dropoff_stations: FrozenSet[Tuple[int, int]]

    # Precomputed set of all walkable cells (no static obstacle)
    walkable_cells: FrozenSet[Tuple[int, int]] = field(init=False)

    def __post_init__(self) -> None:
        all_cells = frozenset(
            (x, y)
            for x in range(self.width)
            for y in range(self.height)
        )
        object.__setattr__(self, "walkable_cells", all_cells - self.static_obstacles)

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_static_blocked(self, x: int, y: int) -> bool:
        return (x, y) in self.static_obstacles

    def is_charging_station(self, x: int, y: int) -> bool:
        return (x, y) in self.charging_stations

    def nearest_charger(self, x: int, y: int) -> Tuple[int, int] | None:
        """Return the Euclidean-nearest charging station, or None if none exist."""
        if not self.charging_stations:
            return None
        return min(
            self.charging_stations,
            key=lambda c: (c[0] - x) ** 2 + (c[1] - y) ** 2,
        )


def build_default_world(width: int = 30, height: int = 30) -> WorldConfig:
    """
    Build the default SCHEMA.md warehouse:
      - static obstacles: column x=5, rows y=5..7
      - charging stations: (0,0) and (29,29)
      - pickup: (4,22)
      - dropoff: (27,3)
    """
    static_obstacles: Set[Tuple[int, int]] = {
        (5, 5), (5, 6), (5, 7),
    }
    # Generate a more interesting warehouse with shelving rows
    # Rows of shelves at y=10,12,14,16,18,20 for x=7..22
    for shelf_y in range(10, 22, 2):
        for shelf_x in range(7, 23):
            static_obstacles.add((shelf_x, shelf_y))

    return WorldConfig(
        width=width,
        height=height,
        cell_size_m=1.0,
        static_obstacles=frozenset(static_obstacles),
        charging_stations=frozenset({(0, 0), (29, 29)}),
        pickup_stations=frozenset({(4, 22)}),
        dropoff_stations=frozenset({(27, 3)}),
    )
