# Backend Architecture — SIH2026 Member 4

**Project:** SIH26123 — Edge-AI Based Distributed Fleet Coordination for Smart Warehouses  
**Author:** Member 4 — Backend & Edge Simulation Broker

---

## Design Philosophy

1. **Everything in memory.** No database in the hot path. PostgreSQL is not used.
2. **Single asyncio loop.** One simulation task, one event loop thread. No locks needed.
3. **Backend is authoritative.** Frontend is a display; it never writes to simulation state.
4. **Interfaces everywhere.** Every algorithm is behind an adapter so team members can swap implementations.
5. **No unnecessary infrastructure.** No Redis, no Kafka, no microservices for the MVP.

---

## Module Map

```
backend/
└── app/
    ├── main.py                    ← FastAPI app + lifespan (service wiring)
    │
    ├── core/
    │   ├── config.py              ← Pydantic Settings (env vars → typed config)
    │   └── logging.py             ← Structured logging setup
    │
    ├── models/                    ← Pure Python dataclasses (hot-path structs)
    │   ├── robot.py               ← Robot, RobotState, Heading, PathNode
    │   ├── task.py                ← Task, TaskStatus
    │   ├── obstacle.py            ← TemporaryObstacle
    │   ├── reservation.py         ← ReservationKey, ReservationTable type aliases
    │   └── world.py               ← WorldConfig (precomputed, immutable)
    │
    ├── schemas/                   ← Pydantic models (API I/O validation + serialization)
    │   ├── robot.py
    │   ├── task.py
    │   ├── websocket.py
    │   └── obstacle.py
    │
    ├── services/                  ← Business logic (hot path + adapters)
    │   ├── fleet_state.py         ← AUTHORITATIVE simulation state container
    │   ├── simulation_engine.py   ← 14-step tick pipeline + asyncio clock
    │   ├── reservation_manager.py ← Space-time reservation table (O(1) ops)
    │   ├── conflict_manager.py    ← SCHEMA.md §13 conflict detection + resolution
    │   ├── task_manager.py        ← Task lifecycle + pluggable assignment
    │   ├── planner_adapter.py     ← Space-Time A* + external planner interface
    │   └── telemetry.py           ← EMA-smoothed performance metrics
    │
    ├── api/
    │   ├── tasks.py               ← POST /api/task/inject
    │   ├── simulation.py          ← POST /api/simulation/{start,pause,reset}
    │   ├── chaos.py               ← POST /api/chaos/toggle
    │   ├── robots.py              ← GET /api/robots/
    │   ├── websocket.py           ← WS /ws/fleet
    │   └── chaos_and_world.py     ← POST/DELETE /api/obstacles, GET /api/world, GET /api/metrics
    │
    ├── websocket/
    │   └── connection_manager.py  ← Multi-client WS broadcast (no-block)
    │
    └── tests/
        ├── test_all.py            ← Full test suite (pytest)
        └── benchmark.py           ← Performance benchmark script
```

---

## Data Flow — One Tick

```
asyncio.sleep() expires
        │
        ▼
SimulationEngine._tick()
        │
        ├─ 1. tick++ 
        ├─ 2. flush pending tasks & obstacles from REST queue
        ├─ 3. expire timed-out obstacles
        ├─ 4. assign PENDING tasks to IDLE robots (TaskManager)
        ├─ 5. ConflictManager.detect_and_resolve()
        │       └─ for each nearby pair: score, yield loser, mark _needs_replan
        ├─ 6. replan robots with _needs_replan=True (PlannerAdapter)
        ├─ 7. plan newly assigned robots with no path
        ├─ 8. plan charging routes for low-battery robots
        ├─ 9. execute each robot: turn? move? wait?
        ├─ 10. update battery (move/turn/wait cost per SCHEMA.md §11)
        ├─ 11. process pickup/dropoff (1-tick operations per §10)
        ├─ 12. process charging (+5%/tick per §12, stop at 80%)
        ├─ 13. update priority scores
        ├─ 14. purge past reservations
        ├─ 15. update telemetry
        └─ 16. broadcast TICK_UPDATE → ConnectionManager → all WS clients
```

---

## Integration Points for Other Team Members

### Member 2 — Pathfinding

**Interface:** `AbstractPlannerAdapter` in `services/planner_adapter.py`

```python
class AbstractPlannerAdapter(abc.ABC):
    def find_path(
        self,
        start: Tuple[int, int],
        goal: Tuple[int, int],
        current_tick: int,
        reservation_table: ReservationTable,
        world: WorldConfig,
        temp_blocked: Set[Tuple[int, int]],
    ) -> List[Dict]:  # [{x, y, t}, ...]
        ...
```

**To plug in:**
1. Implement `AbstractPlannerAdapter`
2. Set `PLANNER_BACKEND=external` + `PLANNER_URL=http://...` in `.env`
3. OR replace `get_planner_adapter()` to return your class directly

The backend uses Space-Time A* as the built-in fallback.

---

### Member 3 — Conflict Resolution / Task Assignment

**Conflict interface:** `ConflictManager` in `services/conflict_manager.py`

The `detect_and_resolve()` method follows SCHEMA.md §13 exactly.
Replace it by constructing a different `ConflictManager` subclass in `main.py`.

**Task assignment interface:** `AbstractTaskAssigner` in `services/task_manager.py`

```python
class AbstractTaskAssigner(abc.ABC):
    def assign(
        self,
        task: Task,
        robots: Dict[str, Robot],
        active_tasks: Dict[str, Task],
    ) -> Optional[str]:  # robot_id or None
        ...
```

Hot-swap at runtime: `task_manager.replace_assigner(YourAssigner())`

---

### Member 1 — Frontend

WebSocket endpoint: `ws://localhost:8000/ws/fleet`

Receives `TICK_UPDATE` every 500ms (SCHEMA.md §16 exact contract).

REST endpoints documented in `/docs` (Swagger UI).

CORS is open for all origins in dev mode.

---

## Latency Budget

For a 30×30 warehouse with 10 robots at 500ms tick:

| Stage | Budget | Typical Actual |
|-------|--------|----------------|
| Conflict detection | < 1ms | ~0.2ms |
| Pathfinding (10 robots) | < 5ms | ~1ms |
| Movement execution | < 1ms | ~0.1ms |
| Serialization | < 2ms | ~0.5ms |
| WS broadcast | < 10ms | ~1ms |
| **Total tick** | **< 20ms** | **~3ms** |

This leaves 480ms of headroom in the 500ms budget.

---

## Memory Layout

| Structure | Type | Size (10 robots) |
|-----------|------|-----------------|
| `robots` dict | `Dict[str, Robot]` | ~10 KB |
| `reservation_table` | `Dict[tuple, str]` | ~50 KB (30-step paths × 10 robots) |
| `temp_obstacles` | `Dict[str, TemporaryObstacle]` | negligible |
| `active_conflicts` | `List[ConflictRecord]` | negligible |
| WS payload (JSON) | `str` | ~5 KB/tick |

Total hot-path memory: **< 1 MB** for 10 robots.
Scales linearly with fleet size. 100 robots ≈ 10 MB.

---

## Determinism Guarantee

Given the same:
- Initial `FleetState` (robots, tasks, world)
- Same planner outputs
- No chaos mode

The simulation will produce identical robot positions at every tick.

Chaos mode uses `random.randint()` isolated to the WebSocket broadcast only.
It never modifies the simulation state.
