# SIH-2026: Decentralized Edge-AI Multi-Robot Warehouse Coordination

Smart India Hackathon 2026 — Problem Statement PS26123.
An edge-first, collision-free autonomous fleet coordination platform featuring autonomous process-isolated AMRs, peer-to-peer UDP messaging with HMAC-SHA256 authentication, Contract-Net task bidding, Space-Time A* pathfinding, and sub-millisecond Graph Neural Network (GNN) priority arbitration.

---

## Documentation Index

### System Design & Technical Defense
- **System Architecture**: [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md)
- **Data & Telemetry Schemas**: [`docs/architecture/SCHEMA.md`](docs/architecture/SCHEMA.md)
- **Judge Q&A & Technical Defense Guide**: [`docs/JUDGE_QA.md`](docs/JUDGE_QA.md)
- **01. Architecture Overview (C4 Diagrams & Decentralization Boundary)**: [`docs/system-design/01-architecture-overview.md`](docs/system-design/01-architecture-overview.md)
- **02. Job Lifecycle Sequence Diagram**: [`docs/system-design/02-sequence-job-lifecycle.md`](docs/system-design/02-sequence-job-lifecycle.md)
- **03. Conflict Resolution & Swap-Collision Fix**: [`docs/system-design/03-sequence-conflict-resolution.md`](docs/system-design/03-sequence-conflict-resolution.md)
- **04. Telemetry Data Flow & Push-vs-Poll Architecture**: [`docs/system-design/04-data-flow.md`](docs/system-design/04-data-flow.md)
- **05. Architecture Decision Records (ADR Log)**: [`docs/system-design/05-adr-log.md`](docs/system-design/05-adr-log.md)
- **06. Edge Hardware Deployment Guide (Pi 4 & Jetson Nano)**: [`docs/system-design/06-edge-deployment-guide.md`](docs/system-design/06-edge-deployment-guide.md)
- **07. PS26123 Compliance Matrix & Verification Traceability**: [`docs/system-design/07-ps26123-compliance-matrix.md`](docs/system-design/07-ps26123-compliance-matrix.md)

### Empirical Benchmarks (PS26123 Proof)
- **Empirical Coordination Benchmark (58%–66% Speedup over Baseline)**: [`docs/benchmarks/coordination_comparison.md`](docs/benchmarks/coordination_comparison.md)
- **Edge Resource & Latency Profile (13.6MB RSS, 0.87ms P95)**: [`docs/benchmarks/edge_resource_profile.md`](docs/benchmarks/edge_resource_profile.md)

### Engineering Reports & Historical Analysis
- **Engineering Fixes & Verification Report**: [`docs/reports/FIXES_APPLIED.md`](docs/reports/FIXES_APPLIED.md)
- **Decentralization Implementation Report**: [`docs/reports/IMPLEMENTATION_REPORT.md`](docs/reports/IMPLEMENTATION_REPORT.md)
- **Production Audit & Telemetry Report (V2)**: [`docs/reports/IMPLEMENTATION_REPORT_V2.md`](docs/reports/IMPLEMENTATION_REPORT_V2.md)
### Part 6 Decentralized Architecture & E2E Verification
- **Part 6 Comprehensive Results & Verification Report**: [`docs/PART6_RESULTS.md`](docs/PART6_RESULTS.md)
- **Part 6 Baseline Report**: [`docs/PART6_BASELINE.md`](docs/PART6_BASELINE.md)
- **Part 6 Master Test Runner**: [`testing/run_all_part6.py`](testing/run_all_part6.py)
- **Part 6 E2E 5 Scenarios Verifier (30x30 & 20x20)**: [`testing/verify_step7_e2e_scenarios.py`](testing/verify_step7_e2e_scenarios.py)

---

## Repository Structure

```text
SIH-2026/
├── backend/                       # FastAPI server, REST/WS APIs & autonomous AMR services
│   ├── backend/
│   │   ├── app/                   # RobotNode, FleetOrchestrator, TelemetryBus, models, ml, api
│   │   │   ├── api/               # REST endpoints (/api/job, /api/simulation)
│   │   │   ├── ml/                # GNN weights & pure NumPy inference
│   │   │   ├── models/            # Robot, Task, FSM, and Telemetry schemas
│   │   │   ├── services/          # RobotNode, FleetOrchestrator, TelemetryBus, Space-Time A*
│   │   │   ├── tests/             # Backend unit tests (67 tests)
│   │   │   └── main.py            # FastAPI lifespan & WebSocket forwarder (/ws/fleet)
│   │   └── pyproject.toml         # Pytest & backend configuration
│   ├── scripts/                   # GNN model training, dataset synthesis, and edge benchmarking
│   ├── Dockerfile
│   ├── Dockerfile.edge
│   └── requirements.txt
├── conflict-engine/               # Symmetric peer conflict detection & arbitration algorithms
│   ├── arbitration.py             # Symmetrical pairwise peer arbitration & lateral nook detours
│   ├── conflict_detector.py       # Space-time vertex & edge swap conflict detection
│   ├── conflict_engine.py         # Pipeline coordination tick
│   ├── models.py                  # Priority & Conflict data structures
│   ├── priority.py                # Formula & GNN priority scoring
│   └── tests/                     # Conflict engine unit tests (25 tests)
├── frontend/                      # React + TypeScript + Vite operations dashboard
│   ├── src/                       # UI components, 2D warehouse canvas, metrics panels
│   └── package.json
├── testing/                       # Centralized E2E, integration, and safety test suites
│   ├── test_decentralization.py   # 6-phase server crash resilience proof
│   ├── test_e2e_fleet_websocket.py# Live WebSocket streaming E2E
│   ├── test_fsm.py                # 11-state deterministic FSM verification
│   ├── test_security.py           # HMAC-SHA256 & ReplayGuard security
│   ├── test_battery_estop.py      # E-stop & battery threshold safety
│   ├── full_integration_test.py   # 50-scenario stress test harness
│   ├── verify_no_swap.py          # Trajectory validator (0 swap / 0 vertex collisions)
│   └── README.md                  # Comprehensive test directory guide
├── docs/                          # System documentation, specifications, reports, and defense
│   ├── architecture/              # Architecture overview & schema specifications
│   ├── benchmarks/                # Empirical benchmark reports & statistical comparison data
│   ├── reports/                   # Bug analyses, refactor notes, implementation audits
│   └── system-design/             # C4 diagrams, sequence diagrams, edge guides
├── data/                          # Priority dataset & training graphs
├── logs/                          # Per-robot runtime execution logs & telemetry snapshots
├── .gitignore
├── requirements.txt               # Unified project Python dependencies
└── README.md                      # Primary entry point with navigation index
```

---

## Warehouse Demo Model

The live demo uses a heterogeneous fleet of three AMR classes:
- **Goods-to-Person** AMRs retrieve items from the central shelving area and deliver them to the outbound export dock.
- **Sorting** AMRs collect batches from the inbound import dock and route them to the sorting zone.
- **Scanning & Audit** AMRs patrol audit checkpoints and report inventory observations.

The 30×30 warehouse features a multi-cell import dock on the west side, a multi-cell export dock on the east side, and six perimeter charging stations. Space-time reservations and symmetrical peer arbitration allow multiple AMRs to queue at shared dock approaches while avoiding cell and swap collisions.

---

## Quick Start

### 1. Create & Activate Virtual Environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux / macOS
python -m venv .venv
source .venv/bin/activate
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 3. Start Backend & Edge Fleet
Run the backend with this working-directory-independent command:
```bash
python -m uvicorn app.main:app --app-dir backend/backend --port 8000
```
> **Note**: Do not pass `--reload` in production or normal development; file-system watchers restart the entire multi-process robot fleet on every disk write.

Alternatively, use the convenience bootstrapping scripts:
- Windows: `scripts\start_backend.bat`
- Linux/macOS: `./scripts/start_backend.sh`

### 4. Start Frontend Operations Dashboard
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` to access the live operations cockpit.

---

## Automated Test Suites

### 1. Unit & Safety Test Suites
```bash
# Run backend and conflict-engine unit tests (92 tests)
python -m pytest backend/backend/app/tests conflict-engine/tests -v

# Run integration and safety test suites in testing/
python -m pytest testing/test_battery_estop.py testing/test_fsm.py testing/test_security.py testing/test_audit_mission.py -v

# Run decentralization & server crash resilience test (spawns 5 OS processes)
python testing/test_decentralization.py

# Verify zero collisions across multi-robot trajectories
python testing/verify_no_swap.py
```

### 2. Master Regression & E2E Suites
```bash
# Run cumulative regression suite (8 suites: idle fleet, motion, camera, collision, map, tasks, E2E)
python testing/run_all_part6.py

# Run comprehensive E2E 5-scenario verifier on both 30x30 and custom 20x20 maps
python testing/verify_step7_e2e_scenarios.py

# Run decentralized task allocation & lease attack suite (6 attack vectors)
python testing/attack_step6_decentralized_tasks.py

# Run map editor format fuzzing & validator suite (5 attack vectors)
python testing/attack_step5_map_editor.py
```

## Future Roadmap: "Later" Ideas & Technical Risks

### 1. Dynamic Multi-Tier Priority Pricing in Contract-Net
- **Opportunity:** AMRs currently bid based on distance, battery penalties, and task urgency. A dynamic surge-pricing mechanism could dynamically adjust bid scores during traffic hotspots to automatically balance aisle density.
- **Risk:** In heavily partitioned or lossy mesh networks, robots might overestimate congestion and defer tasks indefinitely if bidding weights become non-monotonic.

### 2. Continuous Corridor Flow Reservation vs Discrete Vertex Locking
- **Opportunity:** Step 4 enforces narrow corridor locking via discrete Space-Time A* reservations. Moving to directional one-way flow tokens for narrow aisles would permit platooning (multiple robots following each other through a corridor in the same direction).
- **Risk:** platooning requires fine-grained kinematic distance tracking to prevent rear-end collisions during sudden stops.

### 3. Spline Path Smoothing & Non-Holonomic Kinematic Interpolation
- **Opportunity:** The 3D frontend currently interpolates between discrete cell centers using angular slerp. Integrating Hermite spline path smoothing with non-holonomic turning radius limits would provide photorealistic turning curvature.
- **Risk:** Spline paths might clip rack corners if corner clearance bounds are not factored into the bounding-box collision detection.

### 4. WAN-Scale Multi-Warehouse Ledger Replication
- **Opportunity:** The peer-to-peer anti-entropy ledger gossip protocol currently operates over local subnets and simulated mesh networks. Extending this with Merkle-CRDTs would enable cross-warehouse inventory rebalancing without centralized cloud databases.
- **Risk:** High network latency across WAN links could delay consistency convergence, requiring conflict-resolution heuristics for simultaneous cross-dock picks.

