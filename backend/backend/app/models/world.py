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
        if (x, y) in self.charging_stations:
            return "CHARGING_BAY"

        # Storage racking zone
        if 4 <= x <= 25 and 6 <= y <= 22:
            return "GOODS_TO_PERSON_ZONE"

        # Sorting traffic clusters near the perimeter staging lanes
        if (x <= 5 or x >= 24) and (y <= 4 or y >= 24):
            return "SORTING_ZONE"

        # High-speed transit corridors
        if x <= 3 or x >= 26 or y <= 4 or y >= 24:
            return "TRANSIT_HIGHWAY"

        return "GENERAL"


def build_default_world(width: int = 30, height: int = 30) -> WorldConfig:
    """
    Build a realistic automated fulfillment warehouse layout with:
      - 4 modular storage pod banks (double-deep racks) separated by wide pick aisles,
      - 2 vertical cross-highways (x=10, x=19) eliminating bottlenecks,
      - 8 dedicated perimeter charging alcoves (top and bottom),
      - multi-cell inbound receiving docks (West) and outbound shipping docks (East).
    """
    static_obstacles: Set[Tuple[int, int]] = set()

    # 4 Modular Storage Pod Banks (double-deep racks)
    # Bank 1: y=6, 7 | Bank 2: y=11, 12 | Bank 3: y=16, 17 | Bank 4: y=21, 22
    for rack_y in (6, 7, 11, 12, 16, 17, 21, 22):
        for rack_x in range(4, 26):
            # Two main vertical cross-highways at x=10 and x=19
            if rack_x not in (10, 19):
                static_obstacles.add((rack_x, rack_y))

    # 8 Distributed Perimeter Charging Stations (4 North alcoves, 4 South alcoves)
    charging_stations = frozenset({
        (5, 1), (10, 1), (19, 1), (24, 1),
        (5, 28), (10, 28), (19, 28), (24, 28),
    })

    # 3 Inbound Receiving Gates along West Perimeter Wall (x=0), covering 3 tiles each (9 tiles total)
    import_dock = frozenset({
        (0, 8), (0, 9), (0, 10),    # Gate IN-1 (aligned with Pick Aisle 1)
        (0, 13), (0, 14), (0, 15),  # Gate IN-2 (aligned with Pick Aisle 2)
        (0, 18), (0, 19), (0, 20),  # Gate IN-3 (aligned with Pick Aisle 3)
    })

    # 3 Outbound Shipping Gates along East Perimeter Wall (x=29), covering 3 tiles each (9 tiles total)
    export_dock = frozenset({
        (29, 8), (29, 9), (29, 10),    # Gate OUT-1 (aligned with Pick Aisle 1)
        (29, 13), (29, 14), (29, 15),  # Gate OUT-2 (aligned with Pick Aisle 2)
        (29, 18), (29, 19), (29, 20),  # Gate OUT-3 (aligned with Pick Aisle 3)
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
