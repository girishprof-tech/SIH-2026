# SIH2026 — Edge-AI Distributed Fleet Coordination Backend

**Project:** SIH26123 — Edge-AI Based Distributed Fleet Coordination for Smart Warehouses  
**Member 4 Role:** Backend & Edge Simulation Broker

---

## What This Does

This backend runs the authoritative simulation of a 30×30 warehouse with 10 autonomous mobile robots (AMRs). It:

- Runs a deterministic simulation clock (500 ms/tick by default)
- Maintains the authoritative live fleet state **in memory**
- Moves robots, handles turns, reservations, collisions, and charging
- Resolves conflicts using the SCHEMA.md priority formula
- Broadcasts live updates over WebSocket (`/ws/fleet`)
- Exposes REST APIs for task injection, simulation control, and chaos testing

---

## Architecture

```
REST Clients ──────────────────────────────────────────────────┐
                                                                │
POST /api/task/inject    ──► FleetState._pending_tasks queue   │
POST /api/obstacles      ──► FleetState._pending_obstacles     │
POST /api/simulation/*   ──► SimulationEngine control          │
POST /api/chaos/toggle   ──► SimulationEngine chaos flag       │
                                                                │
asyncio event loop ─────────────────────────────────────────────┤
                                                                │
SimulationEngine._run_loop()  (every 500ms)                    │
        │                                                       │
        ▼  (14-step tick pipeline)                              │
  1. tick++                                                     │
  2. flush obstacle/task queues                                 │
  3. expire obstacles                                           │
  4. assign pending tasks                                       │
  5. detect & resolve conflicts  ◄── ConflictManager           │
  6. replan robots that need it  ◄── PlannerAdapter (A*)       │
  7. plan new tasks                                             │
  8. execute movement/turn/wait                                 │
  9. update battery                                             │
  10. process pickup/dropoff                                    │
  11. process charging                                          │
  12. update priority scores                                    │
  13. purge stale reservations                                  │
  14. broadcast TICK_UPDATE ──────────────────────────────────► WebSocket clients
                                                                │
GET /api/metrics ────────────────────────────────────────────── Telemetry
```

---

## Setup

### Prerequisites

- Python 3.11+

### Quick Start

```bash
# Clone / enter the project
cd sih2026-backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env
# Edit .env if needed

# Start the backend
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open: http://localhost:8000/docs (Swagger UI)

### Docker

```bash
docker-compose up --build
```

---

## API Summary

| Method | Route | Description |
|--------|-------|-------------|
| POST | `/api/task/inject` | Inject a new warehouse task |
| GET | `/api/tasks/all` | List all tasks |
| GET | `/api/tasks/{id}` | Get a single task |
| GET | `/api/robots/` | List all robot states |
| GET | `/api/robots/{id}` | Get single robot state |
| POST | `/api/simulation/start` | Start simulation |
| POST | `/api/simulation/pause` | Pause simulation |
| POST | `/api/simulation/reset` | Reset simulation |
| GET | `/api/simulation/status` | Get simulation status |
| POST | `/api/obstacles` | Add temporary obstacle |
| DELETE | `/api/obstacles/{id}` | Remove obstacle |
| GET | `/api/obstacles` | List active obstacles |
| GET | `/api/world` | Get warehouse layout |
| POST | `/api/chaos/toggle` | Toggle chaos mode |
| GET | `/api/metrics` | Performance metrics |
| WS | `/ws/fleet` | Live fleet updates |
| GET | `/health` | Health check |

---

## WebSocket

Connect to `ws://localhost:8000/ws/fleet`

Every tick receives:

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
      "battery_pct": 78.5,
      "current_task_id": "TASK-0042",
      "priority_score": 245,
      "last_updated_tick": 118,
      "path": [{"x": 12, "y": 7, "t": 118}]
    }
  ],
  "active_conflicts": [],
  "temporary_obstacles": []
}
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SIM_TICK_MS` | 500 | Tick duration in ms (50–5000) |
| `FLEET_SIZE` | 10 | Number of robots |
| `GRID_WIDTH` | 30 | Warehouse width |
| `GRID_HEIGHT` | 30 | Warehouse height |
| `PLANNER_BACKEND` | mock | `mock` or `external` |
| `PLANNER_URL` | `` | URL for external planner |
| `LOG_LEVEL` | INFO | Logging level |

---

## Running Tests

```bash
cd backend
pytest app/tests/test_all.py -v
```

## Running Benchmarks

```bash
cd backend
python -m app.tests.benchmark
```
