# SIH26123 — System Design Contract & JSON Schema
### Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses
**Status: LOCKED — do not change without team agreement**

---

## 1. World Rules (Global Constants)

```json
{
  "GRID_WIDTH": 30,
  "GRID_HEIGHT": 30,
  "CELL_SIZE_M": 1.0,
  "TICK_MS": 500,
  "ROBOT_SPEED_TILES_PER_TICK": 1,
  "TURN_COST_TICKS": 1,
  "MOVEMENT_MODE": "4-DIRECTIONAL",
  "ROBOT_FOOTPRINT_TILES": 1
}
```

- **Coordinate origin:** (0,0) = top-left. `+X` = right (East). `+Y` = down (South).
  Frontend Canvas and backend arrays use the SAME convention — never flip Y anywhere in the code.
- **Movement:** Only 4 directions allowed — `NORTH, SOUTH, EAST, WEST`. No diagonals.
  (Keeps collision math simple, avoids corner-clipping edge cases.)
- **Turning:** Changing heading by 90° or 180° costs 1 tick, during which the robot is
  **stationary but still occupies and reserves its current cell**. It cannot be
  "planned through" by another robot during a turn tick.
- **Swap rule:** Two robots may NEVER exchange positions in a single tick
  (i.e., A: (5,5)→(6,5) and B: (6,5)→(5,5) at the same tick is forbidden).
  The path planner and conflict engine must both check for this explicitly.
- **Reservation window:** A robot reserves its ENTIRE computed path (start to goal),
  not just the next few ticks. Reservations are released/recomputed when a path changes.

---

## 2. Robot State Object

Sent by Member 4 (backend) to Member 5 (frontend) every tick, and used internally by
Member 2 (pathfinding) and Member 3 (conflict engine).

```json
{
  "robot_id": "AMR-01",
  "position": { "x": 12, "y": 7 },
  "heading": "NORTH",
  "state": "EN_ROUTE",
  "battery_pct": 78.5,
  "current_task_id": "TASK-0042",
  "path": [
    { "x": 12, "y": 7, "t": 118 },
    { "x": 12, "y": 6, "t": 119 },
    { "x": 13, "y": 6, "t": 120 }
  ],
  "priority_score": 245.0,
  "last_updated_tick": 118
}
```

**`state` must be one of:**
`IDLE | EN_ROUTE | CONFLICT_NEGOTIATING | CHARGING | EMERGENCY_STOP`

**`heading` must be one of:**
`NORTH | SOUTH | EAST | WEST`

---

## 3. Task / Order Object

```json
{
  "task_id": "TASK-0042",
  "pickup": { "x": 4, "y": 22 },
  "dropoff": { "x": 27, "y": 3 },
  "urgency": 3,
  "created_tick": 100,
  "assigned_robot_id": "AMR-01",
  "status": "ASSIGNED"
}
```

- `urgency`: integer 1–5 (5 = most urgent)
- `status` must be one of: `PENDING | ASSIGNED | IN_PROGRESS | COMPLETED`

---

## 4. Priority Score Formula (FINALIZED)

Owned by Member 3. Used to decide who yields during a conflict.

```
PriorityScore = (OrderUrgency × 100)
               + (Battery < 20% ? 500 : 0)
               + (WaitTicksSoFar × 10)
               − (DistanceToGoal × 1)
```

**In plain words:**
- Higher urgency order → higher score → goes first
- Critically low battery → big bonus → goes first (needs to reach charger, don't starve it)
- The longer a robot has already been waiting → its score keeps climbing →
  guarantees it eventually wins (this prevents a robot getting stuck waiting forever,
  called "starvation")
- Being closer to its goal gives a small edge too (nearly-done tasks finish faster)

**Tie-breaker:** if scores are exactly equal, lower `robot_id` wins.

**Yield rule:** the loser waits 1 tick, then re-checks. If still blocked, it may
trigger Member 2's pathfinder to compute an alternate route.

---

## 5. Conflict Trigger Rule

A conflict check fires when:
- Two robots are within a **2-cell radius** of each other, AND
- Their next **2 timestamps** in their reserved paths overlap the same cell OR
  violate the swap rule (Section 1)

---

## 6. WebSocket Message (Backend → Frontend, every tick)

```json
{
  "type": "TICK_UPDATE",
  "tick": 118,
  "timestamp_ms": 1699999999999,
  "robots": [ /* array of Robot State Objects, see Section 2 */ ],
  "active_conflicts": [
    {
      "robot_ids": ["AMR-01", "AMR-03"],
      "cell": { "x": 12, "y": 8 },
      "resolved_by": "AMR-03_yield"
    }
  ]
}
```

---

## 7. REST Endpoints (Member 4 implements, Member 1/6 consume)

### Inject a new pickup task (used live during demo)
```
POST /api/task/inject
Body:
{
  "pickup": { "x": 4, "y": 22 },
  "dropoff": { "x": 27, "y": 3 },
  "urgency": 4
}
```

### Chaos toggle (Member 6, network resilience demo)
```
POST /api/chaos/toggle
Body:
{ "packet_loss_pct": 40 }
```

---

## 8. Pathfinding Function Contract (Member 2 exposes this to everyone)

```python
def find_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    current_tick: int,
    reservation_table: dict[tuple[int, int, int], str]  # (x, y, t) -> robot_id
) -> list[dict]:
    """
    Returns a list of {"x": int, "y": int, "t": int} dicts representing
    the robot's path from start to goal, avoiding reserved cells and
    obeying the swap rule.
    Returns an empty list if no path exists.
    """
```

---

## 9. Obstacles / Static Map

Static obstacles (shelves, walls) are stored as a simple set of blocked cells,
loaded once at startup:

```json
{
  "obstacles": [
    { "x": 5, "y": 5 }, { "x": 5, "y": 6 }, { "x": 5, "y": 7 }
  ],
  "charging_stations": [ { "x": 0, "y": 0 }, { "x": 29, "y": 29 } ],
  "pickup_stations": [ { "x": 4, "y": 22 } ]
}
```

---

## Open items still needing a team decision
- Exact map layout (shelf positions) — Member 1 + Member 5 to finalize before Day 1 ends
- Number of robots to simulate for the demo (suggest starting with 5, stress-test with 15+)
- Battery drain rate per tick (needed for CHARGING state to ever trigger)