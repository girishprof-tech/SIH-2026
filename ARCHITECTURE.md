# System Architecture — SIH-2026 Fleet Coordination

> **Notice:** The canonical, exhaustive system design suite is maintained under [`docs/system-design/`](docs/system-design/):
> - Architecture Overview: [`01-architecture-overview.md`](docs/system-design/01-architecture-overview.md)
> - Edge Hardware Guide: [`06-edge-deployment-guide.md`](docs/system-design/06-edge-deployment-guide.md)
> - BEL PS26123 Compliance Matrix: [`07-ps26123-compliance-matrix.md`](docs/system-design/07-ps26123-compliance-matrix.md)
> - Technical Defense & Judge QA: [`docs/JUDGE_QA.md`](docs/JUDGE_QA.md)

## Overview & Single Source of Truth

In this repository, **only one simulation implementation powers the live application and demo**:
👉 **`backend/backend/app/services/`** (driven by `RobotNode`, `FleetOrchestrator`, and `TelemetryBus`).

The React frontend (`frontend/src/`) connects directly to FastAPI's WebSocket endpoint at `/ws/fleet`, which streams live telemetry from this decentralized Edge-AI simulation.

---

## 1. Authoritative Live Simulation: `backend/backend/app/services/`

The production architecture models true decentralized Edge-AI autonomy on Autonomous Mobile Robots (AMRs):

1. **Process-Per-Robot Autonomy (`multiprocessing.Process`)**:
   - Each robot executes as an independent OS process (`RobotNode` in `backend/backend/app/services/robot_node.py`).
   - Each robot maintains its own internal state machine (`RobotFSM`), battery model, task queue, and local reservation table.
2. **Direct Peer-to-Peer UDP Networking**:
   - Robots communicate directly with nearby peers over non-blocking UDP sockets (`127.0.0.1:9001+`).
   - Every message is authenticated using cryptographic HMAC signatures (`app.security.hmac_envelope`) with ReplayGuard nonce tracking.
3. **Decentralized Space-Time Conflict Arbitration**:
   - Robots broadcast their candidate intended moves before committing.
   - Symmetric pairwise arbitration (`detect_peer_conflict` and `resolve_peer_conflict` in `conflict-engine/`) resolves vertex overlaps and head-on swap conflicts based on standardized SIH priority scores.
   - **Physical Clearance Rule**: When an AMR wins right-of-way into a cell currently occupied by a yielding peer, the winner pauses 1 tick at its current position, guaranteeing zero physical swap collisions.
4. **Decoupled Telemetry Forwarding**:
   - Robots push lightweight state frames to a shared multiprocessing queue.
   - `TelemetryBus` (`app/services/telemetry_bus.py`) aggregates robot frames and writes atomic snapshots to `logs/telemetry_state.json`.
   - The FastAPI backend (`app/main.py`) acts solely as a **read-only telemetry viewer**, broadcasting `TICK_UPDATE` frames over WebSocket (`/ws/fleet`) to web clients. If the FastAPI web server dies or restarts, the autonomous robot processes continue operating without interruption.

---

## 2. Research Prototypes (`/archive/`)

Earlier iterations and exploratory models are preserved under `/archive/` as historical artifacts and evidence of algorithm evolution:

### `archive/pathfinding/`
- **What it is**: An early prototype implementing single-step BFS candidate move ranking and decentralized negotiation on a 30×30 grid.
- **Why it was archived**: It relied on a synchronous in-process Python loop and recursive dependency chain validation (`simulation.py`) designed to output offline static JSON (`sim_output.json`) for a static HTML viewer. The live application instead uses full multi-horizon Space-Time A* planning (`pathfinder.py`) with asynchronous multi-process UDP networking.
- **Core Algorithms Retained**: The robust Space-Time A* planner (`pathfinder.py`), coordinate grid (`grid.py`), and space-time reservation manager (`reservations.py`) have been absorbed into `backend/backend/app/services/` as part of the authoritative runtime.

### `archive/simulation_and_environment/`
- **What it is**: A standalone desktop graphical simulator built with Pygame.
- **Why it was archived**: It was an initial visual sandbox with a rudimentary conflict detector that checked static 3-step geometric path overlap without temporal ($t$) reservation awareness. It cannot interface with the modern web dashboard or multi-process UDP networking.

---

## 3. Directory Map

```
SIH-2026/
├── backend/backend/app/
│   ├── api/                 # FastAPI REST and WebSocket routes (/ws/fleet)
│   ├── services/
│   │   ├── robot_node.py    # Autonomous robot process & P2P UDP engine
│   │   ├── fleet_orchestrator.py # Multi-process fleet manager
│   │   ├── telemetry_bus.py # Telemetry aggregator & atomic snapshot writer
│   │   ├── pathfinder.py    # Space-Time A* pathfinding
│   │   ├── grid.py          # Warehouse coordinate & obstacle grid
│   │   └── reservations.py  # Space-time reservations table
│   └── main.py              # FastAPI lifespan & telemetry forwarder
├── conflict-engine/         # Symmetrical peer conflict detection & arbitration
├── frontend/                # React + TypeScript + Vite operations dashboard
├── archive/                 # Historical prototypes & algorithm exploration
│   ├── pathfinding/         # Prototype 1: Synchronous single-step BFS sim
│   └── simulation_and_environment/ # Prototype 2: Desktop Pygame simulator
├── tests/ & root test_*.py  # Automated regression & fuzz testing suites
```
