# API Reference — SIH2026 Backend

**Base URL:** `http://localhost:8000`  
**WebSocket:** `ws://localhost:8000/ws/fleet`  
**Swagger UI:** `http://localhost:8000/docs`

---

## Task Endpoints

### POST `/api/task/inject`
Inject a new pickup → dropoff task. Returns quickly without waiting for a tick.

**Request:**
```json
{
  "pickup": {"x": 4, "y": 22},
  "dropoff": {"x": 27, "y": 3},
  "urgency": 4
}
```

**Response (201):**
```json
{
  "task_id": "TASK-A3B9C1",
  "pickup": {"x": 4, "y": 22},
  "dropoff": {"x": 27, "y": 3},
  "urgency": 4,
  "status": "PENDING",
  "assigned_robot_id": null,
  "created_tick": 42
}
```

**Errors:** 400 (invalid coords/urgency), 409 (impossible task)

---

### GET `/api/tasks/all`
List all tasks (any status).

### GET `/api/tasks/{task_id}`
Get a single task. **404** if not found.

---

### POST `/api/job`
Create a user-facing job intent instead of a raw pickup/dropoff coordinate pair.

**Request:**
```json
{
  "job_type": "fetch_item",
  "item_id": "SKU-1042",
  "urgency": 4
}
```

**Response (200):**
```json
{
  "job_type": "fetch_item",
  "robot_type": "GOODS_TO_PERSON",
  "task_id": "TASK-A3B9C1",
  "robot_id": "AMR-01",
  "status": "ASSIGNED",
  "message": "Fetch item job assigned to a GOODS_TO_PERSON robot"
}
```

**Job types:**
- `fetch_item`: resolves to a shelving-zone pickup and export-dock dropoff; requires `GOODS_TO_PERSON` robots.
- `sort_batch`: resolves to import-dock pickup and sorting-zone dropoff; requires `SORTING` robots.
- `audit_checkpoint`: schedules a checkpoint task on an idle `SCANNING_AUDIT` robot and returns `audit_id` instead of a `task_id`.

**Errors:** 400 (unsupported job type), 409 (no robot of the required type is available)

---

## Robot Endpoints

### GET `/api/robots/`
List all robot states (matches SCHEMA.md §4 exactly).

### GET `/api/robots/{robot_id}`
Get single robot including internal debug fields. **404** if not found.

---

## Simulation Control

### POST `/api/simulation/start`
Start the simulation clock. **409** if already running.

### POST `/api/simulation/pause`
Pause the simulation. **409** if not running.

### POST `/api/simulation/reset`
Reset to tick=0, reinitialize all robots. Works whether running or paused.

### GET `/api/simulation/status`
```json
{
  "running": true,
  "tick": 118,
  "timestamp_ms": 1699999999999,
  "fleet_size": 10,
  "tick_ms": 500
}
```

---

## Obstacle Endpoints

### POST `/api/obstacles`
Add a temporary obstacle.
```json
{
  "obstacle_id": "TEMP-01",
  "x": 12,
  "y": 8,
  "duration_ticks": 20
}
```
**409** if obstacle_id already exists. **400** if out of bounds or on static obstacle.

### DELETE `/api/obstacles/{obstacle_id}`
Remove obstacle early. **404** if not found.

### GET `/api/obstacles`
List all currently active temporary obstacles.

---

## World

### GET `/api/world`
Returns the full warehouse layout: static obstacles, charging stations, and dock cells for import/export workflows.

**Response body:**
```json
{
  "width": 30,
  "height": 30,
  "cell_size_m": 1.0,
  "static_obstacles": [{"x": 5, "y": 5}, {"x": 5, "y": 6}, {"x": 5, "y": 7}],
  "charging_stations": [
    {"x": 1, "y": 1},
    {"x": 1, "y": 28},
    {"x": 14, "y": 1},
    {"x": 14, "y": 28},
    {"x": 27, "y": 1},
    {"x": 27, "y": 27}
  ],
  "pickup_stations": [
    {"x": 0, "y": 10, "dock_type": "import"},
    {"x": 1, "y": 10, "dock_type": "import"},
    {"x": 2, "y": 10, "dock_type": "import"}
  ],
  "dropoff_stations": [
    {"x": 27, "y": 17, "dock_type": "export"},
    {"x": 28, "y": 17, "dock_type": "export"},
    {"x": 29, "y": 17, "dock_type": "export"}
  ]
}
```

---

## Chaos Mode

### POST `/api/chaos/toggle`
```json
{"packet_loss_pct": 40}
```
Set to 0 to disable. Only affects WebSocket transmission — simulation state stays correct.

### GET `/api/chaos/status`
Returns `{"enabled": true, "packet_loss_pct": 40}`

---

## Metrics

### GET `/api/metrics`
```json
{
  "tick_ms_configured": 500,
  "last_tick_processing_ms": 4.2,
  "planner_latency_ms": 2.1,
  "broadcast_latency_ms": 0.8,
  "connected_clients": 2,
  "active_robots": 10,
  "active_conflicts": 1,
  "replans": 4,
  "total_ticks": 118,
  "task_injection_latency_ms": 0.3,
  "conflict_resolution_latency_ms": 0.2
}
```

---

## WebSocket — `/ws/fleet`

**Connect:** `ws://localhost:8000/ws/fleet`

**Message frequency:** Every simulation tick (default 500ms)

**Message format (SCHEMA.md §16 exact):**
```json
{
  "type": "TICK_UPDATE",
  "tick": 118,
  "timestamp_ms": 1699999999999,
  "robots": [
    {
      "robot_id": "AMR-01",
      "position": {"x": 12, "y": 7},
      "heading": "NORTH",
      "state": "EN_ROUTE",
      "robot_type": "GOODS_TO_PERSON",
      "battery_pct": 78.5,
      "current_task_id": "TASK-0042",
      "priority_score": 245,
      "last_updated_tick": 118,
      "path": [
        {"x": 12, "y": 7, "t": 118},
        {"x": 12, "y": 6, "t": 119}
      ]
    }
  ],
  "active_conflicts": [
    {
      "robot_ids": ["AMR-01", "AMR-03"],
      "cell": {"x": 12, "y": 8},
      "resolved_by": "AMR-03_yield"
    }
  ],
  "temporary_obstacles": [
    {
      "obstacle_id": "TEMP-01",
      "position": {"x": 12, "y": 8},
      "created_tick": 150,
      "expires_at_tick": 170
    }
  ]
}
```

**Robot States:** `IDLE` | `EN_ROUTE` | `CONFLICT_NEGOTIATING` | `CHARGING` | `EMERGENCY_STOP`  
**Robot Types:** `GOODS_TO_PERSON` | `SORTING` | `SCANNING_AUDIT`  
**Headings:** `NORTH` | `SOUTH` | `EAST` | `WEST`

Pickup/dropoff mission tasks are only assigned to `GOODS_TO_PERSON` and `SORTING` robots. `SCANNING_AUDIT` robots are reserved for audit and checkpoint patrol behavior when idle and are excluded from task injection routing.

---

## Error Responses

All errors return JSON:
```json
{"detail": "Human-readable error message"}
```

No stack traces are exposed to clients.
