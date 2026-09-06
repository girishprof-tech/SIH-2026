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

    def zone_for(self, x: int, y: int) -> str:
        """Return a coarse warehouse zone label for rendering and dispatch hints."""
        if (x, y) in self.pickup_stations:
            return "IMPORT_DOCK"
        if (x, y) in self.dropoff_stations:
            return "EXPORT_DOCK"

        # Goods-to-Person traffic clusters around the defined shelving rows.
        if 7 <= x <= 22 and 8 <= y <= 22:
            return "GOODS_TO_PERSON_ZONE"

        # Sorting traffic clusters near the docks and perimeter staging lanes.
        if x <= 5 or x >= 24 or y <= 4 or y >= 24:
            return "SORTING_ZONE"

        return "GENERAL"


def build_default_world(width: int = 30, height: int = 30) -> WorldConfig:
    """
    Build a more realistic warehouse layout with:
      - multi-cell inbound import dock near the west side,
      - multi-cell outbound export dock near the east side,
      - perimeter charging stations distributed for short-range charging, and
      - central shelving rows for Goods-to-Person AMRs.
    """
    static_obstacles: Set[Tuple[int, int]] = {
        (5, 5), (5, 6), (5, 7),
    }
    # Generate a more interesting warehouse with shelving rows.
    for shelf_y in range(10, 22, 2):
        for shelf_x in range(7, 23):
            static_obstacles.add((shelf_x, shelf_y))

    # Distributed perimeter chargers keep robots near the outer lanes instead of forcing
    # long cross-grid travel to reach a single charger.
    charging_stations = frozenset({
        (1, 1), (1, 28),
        (14, 1), (14, 28),
        (27, 1), (27, 27),
    })

    # Multi-cell dock queues: import dock on the west side; export dock on the east side.
    import_dock = frozenset({
        (0, 10), (1, 10), (2, 10),
        (0, 11), (1, 11), (2, 11),
        (0, 12), (1, 12), (2, 12),
    })
    export_dock = frozenset({
        (27, 17), (28, 17), (29, 17),
        (27, 18), (28, 18), (29, 18),
        (27, 19), (28, 19), (29, 19),
    })

    return WorldConfig(
        width=width,
        height=height,
        cell_size_m=1.0,
        static_obstacles=frozenset(static_obstacles),
        charging_stations=charging_stations,
        pickup_stations=import_dock,
        dropoff_stations=export_dock,
    )
