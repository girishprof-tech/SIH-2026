# Unified Warehouse AMR System Schema (v1.0)

**Project:** SIH26123 — Edge-AI Distributed Fleet Coordination for Smart Warehouses

**Status:** MASTER SCHEMA (Backend + Frontend + Simulation)

This document is the single source of truth for backend, frontend, simulation, pathfinding, and conflict-resolution modules.

---

# 1. Global World Configuration

```json
{
  "GRID_WIDTH": 30,
  "GRID_HEIGHT": 30,
  "CELL_SIZE_M": 1.0,
  "WAREHOUSE_WIDTH_M": 30,
  "WAREHOUSE_HEIGHT_M": 30,
  "TICK_MS": 500,
  "ROBOT_SPEED_TILES_PER_TICK": 1,
  "TURN_COST_TICKS": 1,
  "MOVEMENT_MODE": "4_DIRECTIONAL",
  "ROBOT_FOOTPRINT_TILES": 1
}
```

## Coordinate System

| Property | Value |
|---|---|
| Origin | (0,0) Top-Left |
| +X | Right (East) |
| +Y | Down (South) |
| Valid X | 0–29 |
| Valid Y | 0–29 |
| Diagonal Movement | Not Allowed |

---

# 2. Warehouse Map Schema

```json
{
  "obstacles": [
    { "x": 5, "y": 5 },
    { "x": 5, "y": 6 },
    { "x": 5, "y": 7 }
  ],
  "charging_stations": [
    { "x": 0, "y": 0 },
    { "x": 29, "y": 29 }
  ],
  "pickup_stations": [
    { "x": 4, "y": 22 }
  ],
  "dropoff_stations": [
    { "x": 27, "y": 3 }
  ]
}
```

Static objects include shelves, walls, permanently blocked cells, charging stations, pickup stations, and drop-off stations.

---

# 3. Dynamic Obstacle Schema

```json
{
  "obstacle_id": "TEMP-01",
  "position": {
    "x": 12,
    "y": 8
  },
  "created_tick": 150,
  "expires_at_tick": 170
}
```

## Lifecycle

Obstacle Appears → Cell Reserved → Robot Detects Conflict → Path Replanned → Obstacle Expires → Cell Becomes Free

---

# 4. Robot State Schema

```json
{
  "robot_id": "AMR-01",
  "position": {
    "x": 12,
    "y": 7
  },
  "heading": "NORTH",
  "state": "EN_ROUTE",
  "battery_pct": 78.5,
  "current_task_id": "TASK-0042",
  "priority_score": 245,
  "last_updated_tick": 118,
  "path": [
    { "x": 12, "y": 7, "t": 118 },
    { "x": 12, "y": 6, "t": 119 },
    { "x": 13, "y": 6, "t": 120 }
  ]
}
```

## Robot States

- IDLE
- EN_ROUTE
- CONFLICT_NEGOTIATING
- CHARGING
- EMERGENCY_STOP

## Heading Values

- NORTH
- SOUTH
- EAST
- WEST

---

# 5. Task / Order Schema

```json
{
  "task_id": "TASK-0042",
  "pickup": { "x": 4, "y": 22 },
  "dropoff": { "x": 27, "y": 3 },
  "urgency": 4,
  "created_tick": 100,
  "assigned_robot_id": "AMR-01",
  "status": "ASSIGNED"
}
```

**Status Values:** PENDING, ASSIGNED, IN_PROGRESS, COMPLETED

**Urgency:** 1–5

---

# 6. Simulation Clock

| Parameter | Value |
|---|---|
| Tick Duration | 500 ms |
| Robot Speed | 1 tile/tick |
| Turn Cost | 1 tick |
| Pickup Time | 1 tick |
| Drop Time | 1 tick |

---

# 7. Movement Rules

## Forward Movement

Allowed directions:
- North
- South
- East
- West

Maximum movement: **1 adjacent tile per tick**.

## Turning Rule

A 90° or 180° heading change consumes **1 complete tick**.

| Tick | Position | Heading |
|---|---|---|
|118|(12,7)|EAST|
|119|(12,7)|NORTH *(Turning)*|
|120|(12,6)|NORTH|

During the turning tick, the robot remains stationary and reserves its current cell.

---

# 8. Reservation System

Each path reserves **space and time**.

```
(x, y, t)
```

Example:

| Cell | Tick |
|---|---|
|(10,5)|15|
|(11,5)|16|
|(12,5)|17|

Reservations are released whenever a path is replanned.

---

# 9. Collision Rules

## Cell Collision

Two robots may never occupy the same cell during the same tick.

## Swap Collision

```
AMR-1: (5,5) → (6,5)
AMR-2: (6,5) → (5,5)
```

This move is forbidden.

## Boundary Rule

```
0 ≤ x ≤ 29
0 ≤ y ≤ 29
```

Robots cannot leave the warehouse.

## Obstacle Rule

Robots may never enter static or temporary blocked cells.

---

# 10. Pickup & Drop Operations

## Pickup Flow

```
Reach Pickup
      ↓
Occupy Cell
      ↓
 1 Tick Pickup
      ↓
Continue Task
```

## Drop-off Flow

```
Reach Drop-off
       ↓
 1 Tick Drop
       ↓
Task Completed
       ↓
 Become IDLE
```

---

# 11. Battery Model

| Activity | Battery Change |
|---|---:|
| Move | -1.0% |
| Turn | -0.5% |
| Wait | -0.1% |
| Charge | +5.0% |

Battery is clamped between **0% and 100%**.

---

# 12. Charging Behaviour

Charging begins when:

```
Battery < 20%
```

Flow:

```
Low Battery
     ↓
Navigate to Charger
     ↓
CHARGING
     ↓
+5% Every Tick
     ↓
Battery = 80%
     ↓
Resume Tasks
```

Target charge level: **80%**.

---

# 13. Priority & Conflict Resolution

## Conflict Trigger

A conflict is detected when:

- Robots are within a 2-cell radius.
- Future reservations overlap.
- The swap rule is violated.

## Priority Formula

```text
Priority = (Urgency × 100)
         + (Battery <20 ? 500 : 0)
         + (WaitTicks × 10)
         - DistanceToGoal
```

### Tie-breaker

Lower `robot_id` wins.

### Resolution Flow

```
Conflict
    ↓
Calculate Scores
    ↓
Higher Score Wins
    ↓
Loser Waits 1 Tick
    ↓
Recheck
    ↓
Replan if Required
```

---

# 14. Pathfinding Contract

```python
def find_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    current_tick: int,
    reservation_table: dict
) -> list[dict]:
    """
    Returns a list of {x, y, t}
    while avoiding reservations,
    obstacles, and swap collisions.
    """
```

Requirements:

- Avoid blocked cells
- Respect reservation table
- Prevent swap collisions
- Return empty list if no path exists

---

# 15. Fleet Configuration

```json
{
  "fleet_size": 10,
  "robot_prefix": "AMR"
}
```

Default robots:

- AMR-01
- AMR-02
- ...
- AMR-10

The architecture supports larger fleets without modification.

---

# 16. WebSocket Contract

Sent every simulation tick.

```json
{
  "type": "TICK_UPDATE",
  "tick": 118,
  "timestamp_ms": 1699999999999,
  "robots": [],
  "active_conflicts": [
    {
      "robot_ids": ["AMR-01", "AMR-03"],
      "cell": { "x": 12, "y": 8 },
      "resolved_by": "AMR-03_yield"
    }
  ],
  "temporary_obstacles": []
}
```

---

# 17. REST API Contract

## Inject Task

**POST** `/api/task/inject`

```json
{
  "pickup": { "x": 4, "y": 22 },
  "dropoff": { "x": 27, "y": 3 },
  "urgency": 4
}
```

## Chaos Toggle

**POST** `/api/chaos/toggle`

```json
{
  "packet_loss_pct": 40
}
```

---

# 18. Simulation Controls

Frontend controls:

- START
- PAUSE
- RESET
- Add Temporary Obstacle
- Remove Temporary Obstacle

---

# 19. MVP Feature Matrix

## Included

- 30×30 warehouse
- 10 AMRs
- 4-direction movement
- Turning cost
- Reservation system
- Static obstacles
- Temporary obstacles
- Collision prevention
- Swap prevention
- Battery simulation
- Charging
- Pickup & drop-off
- Conflict negotiation
- Priority scoring
- Path replanning
- WebSocket synchronization
- REST task injection

## Excluded

- Diagonal movement
- Moving obstacles
- Wheel physics
- Acceleration & deceleration
- LiDAR simulation
- Sensor noise
- Battery chemistry
- 3D/Gazebo physics
- Detailed robot geometry

---

# 20. System Architecture Summary

```
Simulation Clock (500 ms)
          │
          ▼
Task Manager ───── Pathfinding ───── Conflict Engine
          │              │                 │
          └──────────────┼─────────────────┘
                         ▼
                 Fleet State Engine
                         │
                         ▼
            WebSocket Tick Updates
                         │
                         ▼
              Frontend Visualization
```