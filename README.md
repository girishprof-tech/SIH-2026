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
- **Space-Time Swap Collision Analysis**: [`docs/reports/PEER_BUG_ANALYSIS.md`](docs/reports/PEER_BUG_ANALYSIS.md)
- **Decentralized Concurrency Rationale**: [`docs/reports/REFACTOR_NOTES.md`](docs/reports/REFACTOR_NOTES.md)

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

### 1. Run Automated Test Suites
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

### 2. Start Backend & Fleet
```bash
cd backend/backend
python -m uvicorn app.main:app --port 8000 --reload
```

### 3. Start Frontend Dashboard
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173` to view the live operations control room.
