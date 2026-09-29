# SIH26123 — Part 6 Baseline Report

Generated at: 2026-09-28T23:02:00+05:30
Environment: Windows, Python 3.14.6, pytest 8.3.4, Node v24.18.1, npm 11.16.0

---

## 1. Test Suite & Dependency Verification

### Dependencies
- **Python Dependencies (`requirements.txt`):** All satisfied (FastAPI 0.115.5, pydantic 2.13.4, numpy 2.5.1, networkx 3.6.1, scipy 1.18.1, pytest 8.3.4, etc.).
- **Frontend Dependencies (`frontend/package.json`):** Clean install via `npm install` (246 packages installed, 0 vulnerabilities).
- **Frontend Build (`npm run build`):** Clean compilation via `tsc -b && vite build` (33.43s, 0 errors).

### Full Test Suite Run (`pytest testing/`)
- **Total Tests Collected:** 176 tests across 32 test files.
- **Result:** **176 passed in 129.81s (0:02:09)**.
- **Failures:** 0 failing tests.

---

## 2. Collision & Passthrough Reproducer Runs

### A. Default Fleet Size (10 Robots + 3 Stations)
Command: `python testing/repro_20robot_collision.py 10`
- Target: 320 ticks, tick interval 30ms.
- Final tick reached: 319.
- Active processes: 10 AMR processes (PID 23780 to 5348) + 3 station processes (9601, 9602, 9603).
- **True Passthroughs (> 1 tick cell overlap):** 0
- **Ping-Pong Oscillations (>= 5 ticks):** 0
- **Pod Slot Path Violations:** 0
- **Overall Result:** `PASSED (Zero defects)`

### B. High-Density Fleet Size (20 Robots + 3 Stations)
Command: `python testing/repro_20robot_collision.py 20`
- Target: 320 ticks, tick interval 30ms.
- Final tick reached: 319.
- Active processes: 20 AMR processes (PID 8612 to 7884) + 3 station processes.
- **True Passthroughs (> 1 tick cell overlap):** 0
- **Ping-Pong Oscillations (>= 5 ticks):** 0
- **Pod Slot Path Violations:** 0
- **Overall Result:** `PASSED (Zero defects)`

### C. Live Multi-Process 5-Robot Simulation
Command: `python testing/verify_step4_live_simulation.py`
- Completed 219 ticks across 5 OS processes.
- Check 1 (Ping-Pong): PASSED (0 detected).
- Check 2 (FAILSAFE_HOLD): PASSED (0 stuck).
- Check 3 (Pod slot intersections): PASSED (0 violations).
- Check 4 (Duration): PASSED (219 >= 200 ticks).

---

## 3. Real Telemetry Tick Rate Measurement

Tested steady-state ticking of `FleetOrchestrator(tick_interval_s=cfg.SIM_TICK_MS / 1000.0)` where `SIM_TICK_MS = 500`:
- **Configured Parameter:** `SIM_TICK_MS = 500` (`backend/backend/app/core/config.py:31`).
- **Nominal Rate:** 2.00 ticks/sec (500.0 ms per tick).
- **Measured Empirical Steady-State:** 10 ticks completed in 5.005 seconds -> **2.00 ticks/sec (interval = 500.5 ms per tick)**.
- Note: Standalone test scripts override `tick_interval_s` to 0.02s–0.03s for fast execution, but live system orchestration runs at 2.00 ticks/s.

---

## 4. Confirmation / Refutation of Known Findings F1–F6 & F8

| Finding | Status | Empirical File:Line & Runtime Evidence |
| :--- | :--- | :--- |
| **F1. Robots work without tasks** | **CONFIRMED** | `backend/backend/app/services/robot_node.py:1102-1130`: Any `SCANNING_AUDIT` robot (or robot not named AMR-G2P/AMR-SORT) that is IDLE counts `idle_ticks` and at tick 10 triggers `AuditMission`. Observed live in `test_no_dual_runtime.py` where AMR-08, AMR-09, AMR-10 autonomously launched audit missions at ticks 15, 65, 66 without tasks. Autonomous consolidation (`robot_node.py:287-338`) and transfer (`robot_node.py:339-350`) also trigger unprompted. |
| **F2. Motion is not tick-aware** | **CONFIRMED** | `frontend/src/components/Warehouse3DCanvas.tsx:122-124`: Per-frame `THREE.MathUtils.lerp(..., 0.25)` is frame-rate dependent exponential chasing. Heading uses raw-angle lerp on `HEADING_ROTATION` (`Warehouse3DCanvas.tsx:45-50`), causing long-way rotations across North (π) and West (−π/2). |
| **F3. Robots float & model grounding** | **CONFIRMED** | `frontend/src/components/Warehouse3DCanvas.tsx:134`: Robot group is at `y=0.18`. Wheel cylinders have radius 0.09 at local `y=0.09` (bottom at local 0). Group translation elevates wheel bottoms to world `y=0.18`. Chutes at L422 have box height 0.75 centered at y=0.45, leaving bottom at y=0.075 (floating 7.5cm above floor). |
| **F4. Camera & View Controls** | **CONFIRMED** | `frontend/src/components/Warehouse3DCanvas.tsx:81-92`: Standard `OrbitControls` with right-drag pan only, `target=[0,0,0]`, `maxPolarAngle` clamped. CameraController L75 re-lerps `controlsRef.current.target` to robot position every frame without ground pan or top-down preset. `frontend/src/components/GridCanvas.tsx:109,186` hard-codes caption to "isometric". |
| **F5. Map is hard-coded** | **CONFIRMED** | `backend/backend/app/core/config.py:35-36` and `services/grid.py:29-30`: `GRID_WIDTH = 30, GRID_HEIGHT = 30`. Coordinates hard-coded in `models/world.py:323-340`. `Warehouse3DCanvas.tsx` uses literal `14.5` offsets (`x - 14.5, y - 14.5`) throughout (L118, L389, L408, L448, L508). `api/tasks.py:304` falls back to `(29, 9)`. No map JSON file format or editor exists. |
| **F6. Task allocation is inconsistent** | **CONFIRMED** | Generic `/api/tasks` route dispatches via decentralized contract-net (`api/tasks.py:121`). Job routes (`/job`, L306, L323, L342, L357) pre-select robots centrally via `_pick_idle_robot_for_type` and unicast assign via `try_assign` + `dispatch_to_fleet(target_robot_id=...)`. In `task_manager.py:429-439`, announcement recipients are filtered against `telemetry_state.json` for `state == "IDLE"`, creating central file dependency. |
| **F8. Passthrough & Shelf cell traversal** | **CONFIRMED & CHARACTERIZED** | **Dynamic robot-robot collisions:** 0 defects detected across 320 ticks for both 10 and 20 robots. **Shelf cell cutting BUG CONFIRMED:** `world.static_obstacles` is empty (`models/world.py:346`), `register_shelf_cells` is never invoked in `RobotNode.__init__`, and `grid.py:111` defaults unladen passability to `True`. Robot AMR-02 log (`logs/repro_20robot/robot_AMR-02.log:4-38`) proves AMR-02 pathfound directly through shelf row 7 from (4,7) to (25,7), traversing 20 shelf storage cells without a lifting task. |

---

## 5. Blocking Questions for Part 6

Before proceeding to feature implementation (Steps 1–6), the following questions require explicit alignment:

1. **Sortation & Consolidation Follow-up Tasks (Step 1):**
   When a user creates a task (e.g. a sort task or a G2P fetch task), after it finishes:
   - *Option A (Default Proposal):* System does NOT automatically trigger follow-on tasks (e.g., auto-consolidation or auto-transfer) unless the user's task explicitly specified a chained task flag.
   - *Option B:* The system automatically spawns decentralized follow-on announcements only if the target buffer/chute becomes full as a consequence of the user's completed task.
   *Which behavior do you want?*

2. **Map Schema & Coordinate System (Step 5):**
   - We propose `schema_version: "1.0.0"`.
   - Grid coordinates: origin `(0, 0)` at North-West (top-left) tile, with `x` in `[0, width-1]` (West to East) and `y` in `[0, height-1]` (North to South).
   - In 3D rendering, center offset will be dynamically computed as `(width - 1) / 2.0` and `(height - 1) / 2.0` instead of literal `14.5`.
   *Please confirm if this schema and coordinate convention are approved.*

3. **Charger Assignment & Low-Battery Policy (Step 5 & 6):**
   - In the map format, chargers can specify an optional `assigned_robot_id`.
   - If `assigned_robot_id` is null / unassigned, can any robot with low battery (< 20%) claim that charger dynamically via contract-net lease?
   - Should a robot with an assigned charger prefer its home charger, falling back to an unassigned charger if occupied?

4. **Unclaimed Task Handling (Step 6.2):**
   - If a broadcasted task announcement receives 0 bids after $N$ bid windows (e.g., all eligible robots are busy or low battery):
   - The task state is marked `UNCLAIMED` in the dashboard with a diagnostic reason (e.g. "No eligible robot with sufficient battery / all busy").
   - Should the backend automatically re-announce unclaimed tasks periodically (e.g. every 10 ticks), or remain `UNCLAIMED` until the operator clicks "Retry" in the task UI?
