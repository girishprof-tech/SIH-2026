# Conflict Negotiation & Arbitration Engine — Member 3

**Project:** SIH26123 — Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses  
**Role:** Member 3 — Conflict Resolution & Multi-Robot Arbitration  

---

## 1. Overview

The `conflict-engine` module resolves spatial and temporal conflicts among Autonomous Mobile Robots (AMRs) operating on a 30×30 warehouse grid. It operates deterministically, prevents deadlocks, respects space-time reservations, and eliminates starvation using an aging priority mechanism.

This module acts as the arbitration bridge between:
- **Member 2 (`pathfinding/`):** Computes collision-free routes via Space-Time A* search.
- **Member 4 (`backend/`):** Drives the 500 ms simulation tick loop, maintains live fleet state, and broadcasts updates over WebSockets.

---

## 2. Module File Layout

| File | Purpose |
| :--- | :--- |
| `models.py` | Canonical data definitions (`Robot`, `Task`, `RobotState`, `Heading`) conforming to `SCHEMA.md` §4 and §5. Interoperable with backend objects. |
| `priority.py` | Implementation of `calculate_priority_score()` following the authoritative formula in `SCHEMA.md` §13 with starvation prevention. |
| `conflict_detector.py` | Implements `detect_conflicts()` to identify cell collisions, 2-tick trajectory intersections, and head-on swap violations ($A \rightarrow B$ while $B \rightarrow A$). |
| `arbitration.py` | Implements `resolve_conflict()`. Compares priority scores, yields the lower-priority robot, releases its stale claims, and replans via `find_path_fn`. |
| `conflict_engine.py` | **Master Integration Entry Point.** Exposes `run_conflict_engine_tick()`, the single function invoked by Member 4 once per tick. |
| `INTEGRATION_NOTES.md` | Verbatim audit of teammate code contracts, shared file modifications, and interface compatibility details. |
| `tests/` | Comprehensive test suite covering unit cases, mathematical starvation proofs, swap collision detection, and integration with Member 2 and Member 4. |

---

## 3. Order of Execution (Pipeline)

For the backend simulation teammate, **only one function needs to be called per tick**: `run_conflict_engine_tick(...)`.

Internally, it executes the pipeline in this exact order:

```text
1. Calculate Priority Scores
   │  Iterates over all EN_ROUTE AMRs. Evaluates:
   │  Priority = (Urgency × 100) + (Battery < 20 ? 500 : 0) + (WaitTicks × 10) - DistanceToGoal
   ▼
2. Detect Conflicts
   │  Scans robot pairs within a 2-cell Manhattan radius.
   │  Checks for:
   │    a) CELL_OVERLAP: Same (x, y) target within the next 2 ticks.
   │    b) SWAP_CONFLICT: Next-tick positions trade current cells.
   ▼
3. Arbitrate & Resolve (resolve_conflict)
   │  For each detected conflict:
   │    a) Higher priority score wins and proceeds uninterrupted.
   │    b) Tie-breaker: Lexicographically lower robot_id wins (AMR-01 beats AMR-03).
   │    c) Yielding robot enters CONFLICT_NEGOTIATING; its wait_ticks increments.
   │    d) Yielding robot's reservations are purged from the reservation table.
   │    e) Injected find_path_fn is called with the updated reservation table.
   │    f) Yielding robot receives its alternate route and returns to EN_ROUTE.
   ▼
4. Return Tick Summary
      Returns { "conflicts_found": int, "resolutions": list[dict], "updated_robots": dict }
```

---

## 4. How the Backend Teammate Integrates It

In `simulation_engine.py` (inside the per-tick loop):

```python
from conflict_engine import run_conflict_engine_tick

# Step 5 in the tick loop:
conflict_summary = run_conflict_engine_tick(
    robots=self._state.robots,
    tasks=self._tasks.all_tasks(),
    reservation_table=self._reservations.table,
    current_tick=tick,
    find_path_fn=self._planner.find_path,
)

# Broadcast resolutions over WebSockets:
self._state.active_conflicts = conflict_summary["resolutions"]
```

---

## 5. Running the Test Suite

```bash
cd conflict-engine
python -m pytest tests/ -v
```

All 16 tests cover:
- High vs. low urgency scoring
- Battery bonus calculations
- Numerical proof of starvation prevention
- Distance penalties and tie-breaker arbitration
- Cell overlap and swap conflict detection
- Safe reservation table cleanup
- End-to-end integration with Member 2's Space-Time A* pathfinder and Member 4's backend data models
