"""
World geometry model.

Holds all static warehouse data pre-computed once at startup.
NEVER re-derived inside the simulation hot-path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple


DEFAULT_EXPORT_GATES: Dict[str, List[Tuple[int, int]]] = {
    "OUT-1": [(29, 8), (29, 9), (29, 10)],   # Gate OUT-1
    "OUT-2": [(29, 13), (29, 14), (29, 15)], # Gate OUT-2
    "OUT-3": [(29, 18), (29, 19), (29, 20)], # Gate OUT-3
}

DEFAULT_IMPORT_GATES: Dict[str, List[Tuple[int, int]]] = {
    "IN-1": [(0, 8), (0, 9), (0, 10)],
    "IN-2": [(0, 13), (0, 14), (0, 15)],
    "IN-3": [(0, 18), (0, 19), (0, 20)],
}

DEFAULT_FIXED_STATIONS: Dict[str, Dict[str, Any]] = {
    "IMPORT_STATION": {
        "id": "IMPORT_STATION",
        "name": "Import Station Alpha",
        "role": "IMPORT_STATION",
        "x": 1,
        "y": 14,
        "zone": "IMPORT_DOCK",
        "port": 9601,
        "description": "Fixed command node covering West import dock gates IN-1..3",
    },
    "EXPORT_STATION": {
        "id": "EXPORT_STATION",
        "name": "Export Station Omega",
        "role": "EXPORT_STATION",
        "x": 28,
        "y": 14,
        "zone": "EXPORT_DOCK",
        "port": 9602,
        "description": "Fixed command node covering East shipping dock gates OUT-1..3 and sortation",
    },
    "AUTHORITY_STATION": {
        "id": "AUTHORITY_STATION",
        "name": "Central Authority Station",
        "role": "AUTHORITY_STATION",
        "x": 15,
        "y": 14,
        "zone": "TRANSIT_HIGHWAY",
        "port": 9603,
        "description": "Single point of authority for fault escalations and global overrides (out of normal critical path)",
    },
}


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
    pod_slots: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    sortation_chutes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pick_stations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    sortation_zone: Dict[str, Any] = field(default_factory=dict)
    export_gates: Dict[str, List[Tuple[int, int]]] = field(default_factory=lambda: dict(DEFAULT_EXPORT_GATES))
    import_gates: Dict[str, List[Tuple[int, int]]] = field(default_factory=lambda: dict(DEFAULT_IMPORT_GATES))
    fixed_stations: Dict[str, Dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_FIXED_STATIONS))

    # Precomputed set of all walkable cells (no static obstacle)
    walkable_cells: FrozenSet[Tuple[int, int]] = field(init=False)
    _coords_to_pod: Dict[Tuple[int, int], str] = field(init=False, repr=False)
    _coords_to_chute: Dict[Tuple[int, int], str] = field(init=False, repr=False)
    _coords_to_pick_station: Dict[Tuple[int, int], str] = field(init=False, repr=False)
    _gate_indices: Dict[str, int] = field(init=False, repr=False)
    _lock: threading.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_gate_indices", {gid: 0 for gid in self.export_gates})

        # PART A Validation: Check that every chute has a valid gate_id
        for chute_id, info in self.sortation_chutes.items():
            gate_id = info.get("gate_id")
            if not gate_id:
                raise ValueError(
                    f"Configuration Error: Chute '{chute_id}' is missing a required 'gate_id' mapping!"
                )
            if gate_id not in self.export_gates:
                raise ValueError(
                    f"Configuration Error: Chute '{chute_id}' references invalid gate_id '{gate_id}'. "
                    f"Available export gates are: {list(self.export_gates.keys())}."
                )

        all_cells = frozenset(
            (x, y)
            for x in range(self.width)
            for y in range(self.height)
        )
        object.__setattr__(self, "walkable_cells", all_cells - self.static_obstacles)
        object.__setattr__(
            self,
            "_coords_to_pod",
            {pos: shelf_id for shelf_id, pos in self.pod_slots.items()}
        )
        object.__setattr__(
            self,
            "_coords_to_chute",
            {(info["x"], info["y"]): chute_id for chute_id, info in self.sortation_chutes.items()}
        )
        object.__setattr__(
            self,
            "_coords_to_pick_station",
            {(info["x"], info["y"]): ps_id for ps_id, info in self.pick_stations.items()}
        )

    def gate_position(self, gate_id: str) -> Tuple[int, int]:
        """
        PART A: Returns a rotating cell from that gate's coordinate cluster so
        simultaneous consolidations to the same gate do not collide on one tile.
        """
        if gate_id not in self.export_gates:
            raise ValueError(f"Unknown gate_id '{gate_id}'. Valid gates: {list(self.export_gates.keys())}")
        cluster = self.export_gates[gate_id]
        with self._lock:
            idx = self._gate_indices.get(gate_id, 0)
            pos = cluster[idx % len(cluster)]
            self._gate_indices[gate_id] = idx + 1
        return pos

    def shelf_at(self, x: int, y: int) -> Optional[str]:
        """Return shelf_id of pod slot at (x, y), or None if not a pod slot."""
        return self._coords_to_pod.get((x, y))

    def chute_at(self, x: int, y: int) -> Optional[str]:
        """Return chute_id of sortation chute at (x, y), or None."""
        return self._coords_to_chute.get((x, y))

    def pick_station_at(self, x: int, y: int) -> Optional[str]:
        """Return pick_station_id at (x, y), or None."""
        return self._coords_to_pick_station.get((x, y))

    def chute_for_destination(self, destination_zone: str) -> str:
        """
        Returns chute_id matching destination_zone.
        If no matching chute is found, defaults to the designated OVERFLOW chute (CHUTE-08).
        """
        for chute_id, info in self.sortation_chutes.items():
            if info.get("destination_zone") == destination_zone:
                return chute_id
        # Fallback overflow chute
        return "CHUTE-08"

    # PART B: Pick Station Buffer Management
    def deposit_carton_to_pick_station(self, carton: Any, preferred_station_id: Optional[str] = None) -> Optional[str]:
        """
        Places a Carton into a pick-station buffer with capacity backpressure.
        Returns station_id where carton was placed, or None if all pick stations are full.
        """
        with self._lock:
            # Check preferred station first if specified
            if preferred_station_id and preferred_station_id in self.pick_stations:
                st = self.pick_stations[preferred_station_id]
                buf = st.setdefault("buffer", [])
                if len(buf) < st.get("capacity", 4):
                    buf.append(carton)
                    return preferred_station_id

            # Find nearest or first station with available capacity
            candidates = sorted(
                self.pick_stations.items(),
                key=lambda item: len(item[1].setdefault("buffer", [])),
            )
            for ps_id, st in candidates:
                buf = st.setdefault("buffer", [])
                if len(buf) < st.get("capacity", 4):
                    buf.append(carton)
                    return ps_id

        # Saturated buffer: backpressure
        return None

    def take_carton_from_pick_station(self, station_id: str) -> Optional[Any]:
        """Pops and returns the next carton from station_id's buffer, or None if empty."""
        with self._lock:
            st = self.pick_stations.get(station_id)
            if not st:
                return None
            buf = st.setdefault("buffer", [])
            if buf:
                return buf.pop(0)
            return None

    def get_non_empty_pick_stations(self) -> List[str]:
        """Returns list of pick station IDs that currently hold at least one carton."""
        with self._lock:
            return [
                ps_id
                for ps_id, st in self.pick_stations.items()
                if len(st.get("buffer", [])) > 0
            ]

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

        # Explicit Sortation Section (Part B)
        if 21 <= x <= 27 and 2 <= y <= 5:
            return "SORTING_ZONE"

        # Storage pod yard zone
        if 4 <= x <= 25 and 6 <= y <= 22:
            return "GOODS_TO_PERSON_ZONE"

        # Perimeter staging lanes
        if (x <= 5 or x >= 24) and (y <= 4 or y >= 24):
            return "SORTING_ZONE"

        # High-speed transit corridors
        if x <= 3 or x >= 26 or y <= 4 or y >= 24:
            return "TRANSIT_HIGHWAY"

        return "GENERAL"


def build_default_world(width: int = 30, height: int = 30) -> WorldConfig:
    """
    Build a realistic automated fulfillment warehouse layout with:
      - 4 modular storage pod banks (Pod Yard with 176 addressable pod slots POD-A01..POD-D44)
        laid out as accessible double-deep pods with North/South aisle entries,
      - 1 Bounded Sortation Section (Part B: x=22..27, y=2..5) containing all 8 sortation chutes,
      - 1 Pick-Station Row (Part B: x=20, y=2,3,4) with output buffers for G2P-to-Sort handoff,
      - 3 Outbound Shipping Gates (OUT-1, OUT-2, OUT-3) with rotating coordinate clusters,
      - 2 vertical cross-highways (x=10, x=19) eliminating bottlenecks,
      - 8 dedicated perimeter charging alcoves (top and bottom),
      - multi-cell inbound receiving docks (West) and outbound shipping docks (East).
    """
    pod_slots: Dict[str, Tuple[int, int]] = {}

    # Pod Yard Layout: 4 Banks (A, B, C, D)
    # Bank A: rows 6, 7 | Bank B: rows 11, 12 | Bank C: rows 16, 17 | Bank D: rows 21, 22
    bank_rows = [
        ("A", (6, 7)),
        ("B", (11, 12)),
        ("C", (16, 17)),
        ("D", (21, 22)),
    ]

    for bank_letter, rows in bank_rows:
        slot_idx = 1
        for rack_y in rows:
            for rack_x in range(4, 26):
                # Two main vertical cross-highways at x=10 and x=19
                if rack_x not in (10, 19):
                    shelf_id = f"POD-{bank_letter}{slot_idx:02d}"
                    pod_slots[shelf_id] = (rack_x, rack_y)
                    slot_idx += 1

    # PART B: 8 Dedicated Sortation Chutes inside the explicit bounded sortation rectangle (x=22..27, y=2..5)
    # With PART A gate_id mapping to OUT-1, OUT-2, OUT-3
    sortation_chutes: Dict[str, Dict[str, Any]] = {
        "CHUTE-01": {"name": "CHUTE-01", "x": 23, "y": 2, "destination_zone": "ZONE_NORTH", "gate_id": "OUT-1", "capacity": 10, "current_count": 0},
        "CHUTE-02": {"name": "CHUTE-02", "x": 24, "y": 2, "destination_zone": "ZONE_EAST", "gate_id": "OUT-1", "capacity": 10, "current_count": 0},
        "CHUTE-03": {"name": "CHUTE-03", "x": 25, "y": 2, "destination_zone": "ZONE_SOUTH", "gate_id": "OUT-2", "capacity": 10, "current_count": 0},
        "CHUTE-04": {"name": "CHUTE-04", "x": 26, "y": 2, "destination_zone": "ZONE_WEST", "gate_id": "OUT-2", "capacity": 10, "current_count": 0},
        "CHUTE-05": {"name": "CHUTE-05", "x": 23, "y": 4, "destination_zone": "ZONE_EXPRESS", "gate_id": "OUT-2", "capacity": 10, "current_count": 0},
        "CHUTE-06": {"name": "CHUTE-06", "x": 24, "y": 4, "destination_zone": "ZONE_REGIONAL", "gate_id": "OUT-3", "capacity": 10, "current_count": 0},
        "CHUTE-07": {"name": "CHUTE-07", "x": 25, "y": 4, "destination_zone": "ZONE_INTERNATIONAL", "gate_id": "OUT-3", "capacity": 10, "current_count": 0},
        "CHUTE-08": {"name": "CHUTE-08", "x": 26, "y": 4, "destination_zone": "OVERFLOW", "gate_id": "OUT-3", "capacity": 20, "current_count": 0},
    }

    # PART B: Pick Stations at the pod yard's east boundary near x=19 cross-highway
    pick_stations: Dict[str, Dict[str, Any]] = {
        "PICK-01": {"name": "PICK-01", "x": 20, "y": 2, "capacity": 4, "buffer": []},
        "PICK-02": {"name": "PICK-02", "x": 20, "y": 3, "capacity": 4, "buffer": []},
        "PICK-03": {"name": "PICK-03", "x": 20, "y": 4, "capacity": 4, "buffer": []},
    }

    sortation_zone: Dict[str, Any] = {
        "bounds": {"min_x": 22, "max_x": 27, "min_y": 2, "max_y": 5},
        "entrances": [(21, 3), (21, 4)],
    }

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
        static_obstacles=frozenset(),  # Pod slots are enterable by robots; no fixed interior obstacles
        charging_stations=charging_stations,
        pickup_stations=import_dock,
        dropoff_stations=export_dock,
        pod_slots=pod_slots,
        sortation_chutes=sortation_chutes,
        pick_stations=pick_stations,
        sortation_zone=sortation_zone,
        export_gates=dict(DEFAULT_EXPORT_GATES),
        import_gates=dict(DEFAULT_IMPORT_GATES),
        fixed_stations=dict(DEFAULT_FIXED_STATIONS),
    )


def build_world_from_map_dict(map_data: Dict[str, Any]) -> Tuple[WorldConfig, List[Dict[str, Any]]]:
    """Derive complete warehouse WorldConfig and robot starts from versioned map dictionary."""
    grid_info = map_data.get("grid", {})
    width = int(grid_info.get("width", 30))
    height = int(grid_info.get("height", 30))
    cell_size = float(grid_info.get("cell_size_m", 1.0))

    static_obstacles = frozenset(
        (int(c["x"]), int(c["y"]))
        for c in map_data.get("blocked_cells", [])
        if isinstance(c, dict) and "x" in c and "y" in c
    )

    charging_stations = frozenset(
        (int(c["x"]), int(c["y"]))
        for c in map_data.get("chargers", [])
        if isinstance(c, dict) and "x" in c and "y" in c
    )

    pod_slots: Dict[str, Tuple[int, int]] = {
        s["id"]: (int(s["x"]), int(s["y"]))
        for s in map_data.get("shelves", [])
        if isinstance(s, dict) and "id" in s
    }

    pick_stations: Dict[str, Dict[str, Any]] = {
        ps["id"]: {**ps, "x": int(ps["x"]), "y": int(ps["y"])}
        for ps in map_data.get("pick_stations", [])
        if isinstance(ps, dict) and "id" in ps
    }

    import_gates: Dict[str, List[Tuple[int, int]]] = {}
    for g in map_data.get("entry_gates", []):
        gid = g.get("id", "IN")
        cells = g.get("cells", [])
        if not cells and "x" in g and "y" in g:
            cells = [{"x": g["x"], "y": g["y"]}]
        import_gates[gid] = [(int(c["x"]), int(c["y"])) for c in cells if "x" in c and "y" in c]

    export_gates: Dict[str, List[Tuple[int, int]]] = {}
    for g in map_data.get("exit_gates", []):
        gid = g.get("id", "OUT")
        cells = g.get("cells", [])
        if not cells and "x" in g and "y" in g:
            cells = [{"x": g["x"], "y": g["y"]}]
        export_gates[gid] = [(int(c["x"]), int(c["y"])) for c in cells if "x" in c and "y" in c]

    sortation_chutes: Dict[str, Dict[str, Any]] = {}
    for s in map_data.get("sorting_stations", []):
        if not isinstance(s, dict) or "id" not in s:
            continue
        sx = int(s["x"])
        sy = int(s["y"])
        gate_id = s.get("gate_id") or s.get("destination_zone")
        if not gate_id or gate_id not in export_gates:
            nearest_gid = None
            min_dist = float("inf")
            for eg_id, eg_cells in export_gates.items():
                for gx, gy in eg_cells:
                    d = abs(sx - gx) + abs(sy - gy)
                    if d < min_dist:
                        min_dist = d
                        nearest_gid = eg_id
            if nearest_gid:
                gate_id = nearest_gid
            elif export_gates:
                gate_id = list(export_gates.keys())[0]
            else:
                gate_id = "OUT-1"
        sortation_chutes[s["id"]] = {
            **s,
            "x": sx,
            "y": sy,
            "gate_id": gate_id,
            "destination_zone": s.get("destination_zone") or gate_id,
        }

    pickup_stations = frozenset(c for cells in import_gates.values() for c in cells)
    dropoff_stations = frozenset(c for cells in export_gates.values() for c in cells)

    fixed_stations = {
        s["id"]: {**s, "x": int(s["x"]), "y": int(s["y"])}
        for s in map_data.get("fixed_stations", [])
        if isinstance(s, dict) and "id" in s
    }
    if not fixed_stations:
        # Position fixed stations (Import/Export/Authority) from entry/exit gates
        if import_gates:
            first_gate_cells = list(import_gates.values())[0]
            ix, iy = first_gate_cells[0]
            import_x = min(ix + 1 if ix == 0 else ix, width - 1)
            import_y = min(iy, height - 1)
        else:
            import_x = min(1, width - 1)
            import_y = min(14, max(0, height // 2))

        if export_gates:
            first_exit_cells = list(export_gates.values())[0]
            ex, ey = first_exit_cells[0]
            export_x = max(ex - 1 if ex == width - 1 else ex, 0)
            export_y = min(ey, height - 1)
        else:
            export_x = max(0, width - 2)
            export_y = min(14, max(0, height // 2))

        auth_x = max(0, min(width - 1, width // 2))
        auth_y = max(0, min(height - 1, height // 2))

        fixed_stations = {
            "IMPORT_STATION": {
                "id": "IMPORT_STATION",
                "name": "Import Station Alpha",
                "role": "IMPORT_STATION",
                "x": import_x,
                "y": import_y,
                "zone": "IMPORT_DOCK",
                "port": 9601,
            },
            "EXPORT_STATION": {
                "id": "EXPORT_STATION",
                "name": "Export Station Omega",
                "role": "EXPORT_STATION",
                "x": export_x,
                "y": export_y,
                "zone": "EXPORT_DOCK",
                "port": 9602,
            },
            "AUTHORITY_STATION": {
                "id": "AUTHORITY_STATION",
                "name": "Authority Station Central",
                "role": "AUTHORITY_STATION",
                "x": auth_x,
                "y": auth_y,
                "zone": "GENERAL",
                "port": 9603,
            },
        }

    sortation_zone = map_data.get("sortation_zone") or {
        "bounds": {"min_x": int(width * 0.73), "max_x": int(width * 0.9), "min_y": 2, "max_y": 5},
        "entrances": [(int(width * 0.7), 3), (int(width * 0.7), 4)],
    }

    world = WorldConfig(
        width=width,
        height=height,
        cell_size_m=cell_size,
        static_obstacles=static_obstacles,
        charging_stations=charging_stations,
        pickup_stations=pickup_stations,
        dropoff_stations=dropoff_stations,
        pod_slots=pod_slots,
        sortation_chutes=sortation_chutes,
        pick_stations=pick_stations,
        sortation_zone=sortation_zone,
        export_gates=export_gates,
        import_gates=import_gates,
        fixed_stations=fixed_stations,
    )

    robot_starts: List[Dict[str, Any]] = map_data.get("robot_starts", [])
    return world, robot_starts


_ACTIVE_WORLD: Optional[WorldConfig] = None
_ACTIVE_MAP_DATA: Optional[Dict[str, Any]] = None


def get_active_world() -> WorldConfig:
    global _ACTIVE_WORLD
    if _ACTIVE_WORLD is None:
        _ACTIVE_WORLD = build_default_world()
    return _ACTIVE_WORLD


def get_active_map_data() -> Optional[Dict[str, Any]]:
    global _ACTIVE_MAP_DATA
    return _ACTIVE_MAP_DATA


def set_active_world(world: WorldConfig, map_data: Optional[Dict[str, Any]] = None) -> None:
    global _ACTIVE_WORLD, _ACTIVE_MAP_DATA
    _ACTIVE_WORLD = world
    if map_data is not None:
        _ACTIVE_MAP_DATA = dict(map_data)

