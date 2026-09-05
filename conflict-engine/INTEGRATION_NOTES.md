<!--
================================================================================
SIH26123 — Member 3 (Conflict Negotiation & Arbitration Engine)
INTEGRATION NOTES & SYSTEM CONTRACT AUDIT
================================================================================

1. Real find_path Signature (Verbatim from Member 2 pathfinding/pathfinder.py:260-268):
--------------------------------------------------------------------------------
def find_path(
    start: Tuple[int, int],
    goal: Tuple[int, int],
    current_tick: int,
    reservation_table: ReservationTable,
    robot_id: Optional[str] = None,
    start_heading: Optional[str] = None,
    grid: Optional[WarehouseGrid] = None,
) -> List[dict]:

Where:
- ReservationTable = Dict[Tuple[int, int, int], str]  # (x, y, t) -> robot_id
- Return type is List[dict], where each item is {"x": int, "y": int, "t": int}
- Helpers in reservations.py:
    reserve_path(path, robot_id, reservation_table, hold_ticks_at_goal=0)
    release_reservations(robot_id, reservation_table)
    prune_past(reservation_table, current_tick)

2. Real Reservation Table Structure (Verbatim from Member 2 & Member 4):
--------------------------------------------------------------------------------
ReservationTable = Dict[Tuple[int, int, int], str]
Keys are tuples of (x, y, t) where x: int, y: int, t: int.
Values are strings representing robot_id (e.g. "AMR-01").
Used across pathfinding/pathfinder.py, pathfinding/reservations.py, and
backend/backend/app/services/reservation_manager.py.

3. Real Robot / Task Data Structures (Verbatim from Member 4):
--------------------------------------------------------------------------------
From backend/backend/app/models/robot.py:
@dataclass
class Robot:
    robot_id: str
    x: int
    y: int
    heading: Heading
    state: RobotState
    battery_pct: float
    current_task_id: Optional[str]
    priority_score: int
    last_updated_tick: int
    path: List[PathNode] = field(default_factory=list)
    _path_idx: int = field(default=0, repr=False)
    _wait_ticks: int = field(default=0, repr=False)
    _is_turning: bool = False
    _target_heading: Optional[Heading] = None
    _operation_ticks_remaining: int = 0
    _operation_type: Optional[str] = None
    _charger_target: Optional[Tuple[int, int]] = None
    _needs_replan: bool = False
    _replan_count: int = 0

From backend/backend/app/models/task.py:
@dataclass
class Task:
    task_id: str
    pickup_x: int
    pickup_y: int
    dropoff_x: int
    dropoff_y: int
    urgency: int
    created_tick: int
    status: TaskStatus = TaskStatus.PENDING
    assigned_robot_id: Optional[str] = None
    _pickup_done: bool = False
    _assigned_tick: Optional[int] = None
    _completed_tick: Optional[int] = None

4. Structural and Naming Differences & Harmonization:
--------------------------------------------------------------------------------
a) Coordinates / Position:
   - Member 4 stores `x: int, y: int` with `@property def position -> (x, y)`.
   - Reference prompt uses `position: tuple[int, int]`.
   - Harmonization: Added `@position.setter` to Member 4's Robot, and `models.py`
     Robot implements both `position` and `.x, .y` properties.
b) Wait Ticks Field:
   - Member 4 used `_wait_ticks: int = 0`.
   - Reference prompt requires `wait_ticks_so_far: int`.
   - Harmonization: Added `wait_ticks_so_far` property getter/setter to Member 4's
     Robot aliasing `_wait_ticks`.
c) Path Representation:
   - Member 4 uses `List[PathNode]`, where PathNode has attributes `x, y, t`.
   - Member 2 pathfinder returns `List[dict]` `{"x": int, "y": int, "t": int}`.
   - Harmonization: Added `__getitem__` to `PathNode` so `node["x"]` works on both,
     and all conflict engine algorithms accept both `dict` and `PathNode`.
d) Task Pickup / Dropoff:
   - Member 4 uses `pickup_x, pickup_y, dropoff_x, dropoff_y` with `.pickup` and
     `.dropoff` returning `Tuple[int, int]`. This matches the reference schema.
================================================================================
-->

# Integration Notes — Conflict Negotiation & Arbitration Engine (Member 3)

## System Overview

The **Conflict Negotiation & Arbitration Engine** (`conflict-engine/`) provides deterministic, edge-compatible conflict resolution for AMRs operating in a shared 30×30 warehouse floor. It connects Member 2's Space-Time A* pathfinder and Member 4's FastAPI simulation loop.

## Verbatim Code Contracts

### 1. Pathfinding Contract (`pathfinding/pathfinder.py`)
```python
def find_path(
    start: Tuple[int, int],
    goal: Tuple[int, int],
    current_tick: int,
    reservation_table: Dict[Tuple[int, int, int], str],
    robot_id: Optional[str] = None,
    start_heading: Optional[str] = None,
    grid: Optional[WarehouseGrid] = None,
) -> List[dict]:
```

### 2. Reservation Table
```python
ReservationTable = Dict[Tuple[int, int, int], str]  # (x, y, t) -> robot_id
```

### 3. Shared File Modifications
To prevent duplicate/conflicting data structures and ensure seamless interoperability between Member 4's simulation backend and Member 3's conflict engine:

1. **`backend/backend/app/models/robot.py`**:
   - Added `wait_ticks_so_far` getter & setter aliasing `_wait_ticks`.
   - Added `position` setter mapping to `self.x, self.y`.
   - Added `__getitem__` to `PathNode` enabling bracket notation `node["x"]`.
2. **`backend/backend/app/services/fleet_state.py`**:
   - Enhanced `conflicts_as_dicts` to support both `ConflictRecord` instances and dictionary outputs from `resolve_conflict()`.
3. **`backend/backend/app/services/simulation_engine.py`**:
   - Replaced fallback conflict handling in Step 5 with the unified `run_conflict_engine_tick()` entry point.
