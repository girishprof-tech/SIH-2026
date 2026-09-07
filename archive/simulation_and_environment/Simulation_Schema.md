**Purpose**

This document defines the rules that the warehouse simulation and AMR visualization will follow for the SIH MVP.

1. **Warehouse & World**

| **Parameter**           | **MVP Decision**                        |
| ----------------------- | --------------------------------------- |
| Grid Size               | 30 × 30 tiles                           |
| Physical Warehouse Size | 30m × 30m                               |
| Tile Size               | 1m × 1m                                 |
| Coordinate Origin       | (0,0) = Top-Left                        |
| +X direction            | Right / East                            |
| +Y direction            | Down / South                            |
| Coordinate Convention   | Y is Never Inverted                     |
| Valid X Coordinates     | 0–29                                    |
| Valid Y Coordinates     | 0–29                                    |
| Movement Directions     | North, South, East, West Only           |
| Diagonal Movement       | Not Allowed                             |
| Warehouse Boundaries    | Robots May Never Leave the 30 × 30 Grid |

1. **Simulation Clock & Movement**

| **Parameter**              | **MVP Decision**              |
| -------------------------- | ----------------------------- |
| Simulation Tick            | 500 ms                        |
| Robot Nominal Speed        | 1 tile/tick                   |
| Equivalent Physical Speed  | 2 m/s                         |
| Movement Model             | Discrete Grid-Based Movement  |
| Movement per Movement Tick | Maximum 1 Adjacent Cell       |
| Diagonal Movement          | Not Allowed                   |
| Robot Footprint            | 1 × 1 tile                    |
| Robot Position             | Integer Grid Coordinate (x,y) |

1. **AMR Fleet**

**10 AMRs**, identified as follows:

AMR-01

AMR-02

AMR-03

AMR-04

AMR-05

AMR-06

AMR-07

AMR-08

AMR-09

AMR-10

The implementation will use a dynamic fleet, so the architecture is not hardcoded specifically for 10 robots and can later be stress-tested with more robots.

**AMR Representation**

Each AMR will maintain:

- robot_id
- position (x, y)
- heading
- state
- battery_pct
- current_task_id
- path
- priority_score
- last_updated_tick

1. **AMR Dimensions**

For the MVP, **each AMR occupies exactly one grid cell (1m × 1m logical footprint)**.

We will not simulate sub-tile physical geometry such as wheel radius, chassis dimensions or exact rectangular collision boundaries.

1. **AMR Heading**

Allowed Headings:

- NORTH
- SOUTH
- EAST
- WEST

Each AMR will have an explicit initial heading. The heading will always correspond to the direction in which the AMR would move on its next forward movement.

1. **Turning Rules**

**A 90° or 180° heading change costs 1 simulation tick.**

During the turn,

- AMR does not move
- AMR remains in its current cell
- AMR continues to occupy/reserve that cell
- another AMR cannot plan through that cell during the turn tick

**A turn will be represented by repeating the AMR's current position.**

Example:

t = 118 → (12,7), EAST

t = 119 → (12,7), NORTH ← turning

t = 120 → (12,6), NORTH ← movement

This allows the reservation table and collision engine to treat the turn as an actual occupied timestep.

1. **Collision Rules**

**Robot ↔ Robot**

- two AMRs may never occupy the same cell during the same tick

**Swap Collision**

- two AMRs may never exchange positions in the same tick

Example:

AMR-01: (5,5) → (6,5)

Forbidden!

AMR-02: (6,5) → (5,5)

**Robot ↔ Static Obstacle**

- an AMR may never enter a blocked cell

**Robot ↔ Temporary Obstacle**

- an AMR may never enter a cell currently occupied by a temporary obstacle

**Boundary Collision**

- an AMR may never move outside the 30 × 30 warehouse.

1. **Static Warehouse Obstacles**

- Walls
- Shelves/Racks
- Permanently Blocked Areas

They will be represented internally as (x, y) blocked cells.

Example:

{

"obstacles": \[

{"x": 5, "y": 5},

{"x": 5, "y": 6},

{"x": 5, "y": 7}

\]

}

1. **Temporary/Dynamic Obstacles**

**For the MVP, temporary obstacles will be stationary obstacles that appear on a cell for a specified period and then disappear. They will not move in the MVP.**

Example:

{

"obstacle_id": "TEMP-01",

"position": {

"x": 12,

"y": 8

},

"created_tick": 150,

"expires_at_tick": 170

}

**Behaviour**

Obstacle Appears

↓

Cell Becomes Blocked

↓

Affected AMR Detects Invalid Route

↓

Path is Recomputed

↓

AMR Follows New Route

↓

Obstacle Expires

↓

Cell Becomes Available

This gives us a useful dynamic-obstacle/re-routing demonstration without unnecessarily complicating the MVP with moving obstacles.

1. **Pickup & Drop-Off**

Pickup and drop-off locations will be represented as fixed grid cells.

**Pickup**

when an AMR reaches its pickup location,

Arrive

↓

Pickup Operation

↓

1 tick

↓

Continue Task

**Drop-Off**

When an AMR reaches its drop-off location,

Arrive

↓

Drop-off operation

↓

1 tick

↓

Task completed

The AMR remains in the corresponding cell during the operation tick.

1. **Charging Stations**

Charging stations are fixed cells defined in the warehouse map.

Example:

{

"charging_stations": \[

{"x": 0, "y": 0},

{"x": 29, "y": 29}

\]

}

**Charging Behaviour**

For MVP,

Battery < 20%

↓

Low-Battery Condition

↓

AMR Can Be Routed Toward Charger

↓

CHARGING

↓

+5% battery/tick

↓

Battery Reaches 80%

↓

Charging Ends

Charging Target = 80%

1. **Battery Model**

The MVP will use a simplified configurable battery model, not a physical battery simulation.

<div class="joplin-table-wrapper"><table><thead><tr><th><p><strong>Activity</strong></p></th><th><p><strong>Battery Change</strong></p></th></tr></thead><tbody><tr><td><p>Movement</p></td><td><ul><li><strong>1.0% / tick</strong></li></ul></td></tr><tr><td><p>Turning</p></td><td><ul><li><strong>0.5% / tick</strong></li></ul></td></tr><tr><td><p>Waiting</p></td><td><ul><li><strong>0.1% / tick</strong></li></ul></td></tr><tr><td><p>Charging</p></td><td><ul><li><strong>+5.0% / tick</strong></li></ul></td></tr></tbody></table></div>

Battery will be clamped to: 0% ≤ battery_pct ≤ 100%

1. **AMR States**

- IDLE
- EN_ROUTE
- CONFLICT_NEGOTIATING
- CHARGING
- EMERGENCY_STOP

1. **Emergency Stop**

If an AMR enters EMERGENCY_STOP, then

- It immediately stops moving.
- It remains in its current cell.
- Its position does not change.
- It continues to occupy its current cell.
- Normal movement resumes only after the emergency-stop condition is cleared.

1. **Conflict Behaviour**

A conflict is triggered when

- Robots are within a 2-cell radius, and
- Their relevant future reservations conflict, or
- The swap rule would be violated.

**Conflict Resolution:**

Calculate PriorityScore

↓

Higher Score → Priority

Lower Score → Yield

↓

Wait 1 tick

↓

Re-Check

↓

Re-Plan If Necessary

Tie-breaker: Lower robot_id Wins

The simulation will visually indicate active conflicts where possible.

1. **Simulation Controls**

The MVP visualization will provide:

- START
- PAUSE
- RESET

Additionally, where practical:

- Add Temporary Obstacle
- Remove Temporary Obstacle
- And Robot/Task Information Panels

1. **MVP Scope — What We WILL Support?**

The MVP simulation will demonstrate:

✓ 30 × 30 Warehouse

✓ 1m Grid Scale

✓ 10 AMRs

✓ 4-Directional Movement

✓ Robot Heading/Orientation

✓ Discrete 500ms Ticks

✓ Turning Cost

✓ Robot Footprint

✓ Static Obstacles

✓ Temporary Obstacles

✓ Collision Prevention

✓ Swap Prevention

✓ Path Visualization

✓ Multiple Simultaneous AMRs

✓ Amr States

✓ Battery Simulation

✓ Charging

✓ Pickup/Drop-Off

✓ Conflict Visualization

✓ Re-Routing

✓ Start/Pause/Reset

✓ Backend-Compatible Robot State

✓ WebSocket Integration

1. **MVP Scope — What We WILL NOT Support Yet?**

To keep the SIH prototype manageable, the MVP will not simulate:

✗ Diagonal Movement

✗ Moving Temporary Obstacles

✗ Detailed Physical AMR Geometry

✗ Wheel-Level Physics

✗ Real-World Motor Dynamics

✗ Acceleration/Deceleration Physics

✗ Wheel Slip

✗ Sensor Simulation

✗ Lidar Simulation

✗ Realistic Battery Chemistry

✗ 3D Physics

✗ Gazebo-Level Physical Simulation