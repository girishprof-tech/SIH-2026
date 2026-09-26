# 07 — SIH26123 Problem Statement Compliance Matrix

### Problem Statement Title:
**Edge-AI Based Distributed Fleet Coordination for Autonomous Mobile Robots (AMRs) in Smart Warehouses**  
**Organization:** Bharat Electronics Limited (BEL) | Smart India Hackathon 2026  
**Problem Statement ID:** `SIH26123`  

---

## 1. Traceability & Compliance Summary

| # | PS26123 Core Requirement | Compliance Status | Architectural Implementation | Primary Source Files | Automated Test & Empirical Proof |
| :-: | :--- | :---: | :--- | :--- | :--- |
| **1** | **Decentralized Fleet Architecture**<br>No central coordinator in real-time navigation loop; robots act as autonomous edge agents. | **100% COMPLIANT** | Each AMR runs in its own independent OS process (`RobotNode`) executing local FSM, Space-Time A*, and peer UDP messaging. | [`robot_node.py`](backend/backend/app/services/robot_node.py)<br>[`fleet_orchestrator.py`](backend/backend/app/services/fleet_orchestrator.py) | `test_spof_recovery.py`<br>`test_fleet_survives_central_server_death` (100% pass) |
| **2** | **Edge-AI Priority Model**<br>Real AI/ML priority arbitration; offline training to pure NumPy inference on edge hardware. | **100% COMPLIANT** | 2-layer Graph Neural Network (GNN) trained on 5,000 synthetic warehouse conflict graphs; exported to standalone NumPy weights (`.npz`) running in `< 1.0 ms`. | [`priority_gnn_train.py`](backend/backend/app/ml/priority_gnn_train.py)<br>[`priority_gnn_infer.py`](backend/backend/app/ml/priority_gnn_infer.py)<br>[`priority_gnn.py`](backend/backend/app/services/priority_gnn.py) | `test_priority_fallback.py` (7/7 pass)<br>Clamping `[-200, +200]`, audit floor `≤ -1000` |
| **3** | **Collision-Free Pathfinding**<br>Strictly zero cell collisions and zero edge-swap collisions across all operations. | **100% COMPLIANT** | Symmetric 2-phase reservation claims + Space-Time A* + 3-way conflict arbitration + lateral nook evasion + winner bypass. | [`robot_node.py`](backend/backend/app/services/robot_node.py)<br>[`space_time_astar.py`](conflict-engine/space_time_astar.py) | `benchmark_coordination_policies.py` (0 collisions across 90 runs of 30 seeds) |
| **4** | **Decentralized Task Allocation**<br>Dynamic task bidding without central assignment bottlenecks. | **100% COMPLIANT** | Peer-to-peer Contract-Net Bidding Protocol over UDP with deterministic tie-breaking (urgency, battery, distance, robot ID). | [`task_manager.py`](backend/backend/app/services/task_manager.py)<br>[`robot_node.py`](backend/backend/app/services/robot_node.py) | `test_decentralized_task_allocation.py` (4/4 pass) |
| **5** | **Edge Hardware Deployment**<br>Must run on physical edge devices (Raspberry Pi 4 / NVIDIA Jetson Nano) within tight memory/CPU constraints. | **100% COMPLIANT** | Lightweight runtime (Pure Python + NumPy, zero PyTorch/TensorFlow at edge); OCI container, CPU affinity isolation, memory budget < 250 MB. | [`Dockerfile.edge`](backend/Dockerfile.edge)<br>[`emulate_edge_constraints.py`](scripts/emulate_edge_constraints.py)<br>[`06-edge-deployment-guide.md`](docs/system-design/06-edge-deployment-guide.md) | `edge_resource_profile.md`:<br>• Peak RSS: **13.6 MB/AMR** (Budget: 250 MB)<br>• P95 Latency: **0.87 ms** (Budget: 50 ms) |
| **6** | **Empirical Benchmark Proof**<br>≥ 20% task completion speedup over traditional stop-and-wait baseline with p < 0.05. | **100% COMPLIANT** | Rigorous empirical benchmark over 30 random seeds across fleet sizes [5, 10, 15] measuring paired t-test and Wilcoxon signed-rank test. | [`stop_and_wait.py`](backend/backend/app/services/policies/stop_and_wait.py)<br>[`benchmark_coordination_policies.py`](scripts/benchmark_coordination_policies.py) | `coordination_comparison.md`:<br>• Fleet 5: **63.22% speedup** ($p = 1.15 \times 10^{-31}$)<br>• Fleet 10: **66.33% speedup** ($p = 1.78 \times 10^{-30}$)<br>• Fleet 15: **58.04% speedup** ($p = 1.28 \times 10^{-31}$) |
| **7** | **Single Point of Failure (SPOF) Hardening**<br>Central server failure invariance and state recovery. | **100% COMPLIANT** | Append-only write-ahead job journal (`data/job_log.jsonl`) with immediate `os.fsync` durability and startup replay; AMR fleet operates autonomously when server dies. | [`job_journal.py`](backend/backend/app/services/job_journal.py)<br>[`main.py`](backend/backend/app/main.py)<br>[`tasks.py`](backend/backend/app/api/tasks.py) | `test_spof_recovery.py` (4/4 pass) |
| **8** | **Industrial Heterogeneity & Telemetry**<br>Heterogeneous AMR fleet classes, dynamic obstacles, degraded-network throttling, real-time observability. | **100% COMPLIANT** | 3 AMR classes (Goods-to-Person, Sorting, Scanning-Audit), dynamic obstacle avoidance, Degraded Speed Mode (50% throttle on missing ticks), WebSocket live feed. | [`models/robot.py`](backend/backend/app/models/robot.py)<br>[`websocket.py`](backend/backend/app/api/websocket.py)<br>[`GridCanvas.tsx`](frontend/src/components/GridCanvas.tsx) | Live WebSocket telemetry push, Canvas 2D render, chaos packet-loss mode tested |
| **9** | **Decentralized Inventory & Pod Transport**<br>Persistent shelf-pod ledger, real perception audit scans, P2P inventory gossip, Kiva-style G2P pod lifting. | **100% COMPLIANT** | SQLite WAL ledger (`inventory.db`), `INVENTORY_UPDATE` P2P gossip mesh, G2P AMR lifting (`RETRIEVE_POD`/`RETURN_POD`/`PICK_ITEM`), 176-pod addressable pod yard. | [`inventory_ledger.py`](backend/backend/app/services/inventory_ledger.py)<br>[`inventory.py`](backend/backend/app/models/inventory.py)<br>[`robot_node.py`](backend/backend/app/services/robot_node.py) | `test_phase2_inventory.py`<br>`test_phase3_inventory_sync.py`<br>`test_phase4_g2p_pod_transport.py` |
| **10** | **Sortation AMR & WiFi HaLow Uplink**<br>Batch induction, destination sortation chutes, autonomous consolidation triggers, redundant HaLow uplink. | **100% COMPLIANT** | 8 destination sortation chutes (`CHUTE-01`..`08`), autonomous peer-broadcasted `CONSOLIDATE_EXPORT` on chute full, token-bucket throttled HaLow (~150 kbps) uplink. | [`world.py`](backend/backend/app/models/world.py)<br>[`halow_transport.py`](backend/backend/app/transport/halow_transport.py)<br>[`robot_node.py`](backend/backend/app/services/robot_node.py) | `test_phase5_sortation_amr.py`<br>`test_phase6_decentralization_hardening.py` |

---

## 2. Deep-Dive Compliance Breakdown

### Req 1 & 7: Decentralization and Single Point of Failure (SPOF) Elimination
- **PS Clause:** *"The system must avoid centralized bottlenecks and single points of failure. The fleet should operate autonomously even if central communications are disrupted."*
- **Implementation:**
  - In `backend/backend/app/services/robot_node.py`, each AMR node runs in an isolated OS process via Python multiprocessing.
  - Inter-robot communication is performed directly over UDP sockets with HMAC-SHA256 authenticated envelopes and monotonic sequence replay protection.
  - The central FastAPI server acts strictly as a non-authoritative telemetry aggregator and initial job gateway.
  - In `backend/backend/app/services/job_journal.py`, incoming jobs are committed to `data/job_log.jsonl` using a durable write-ahead append-only log with `os.fsync` prior to HTTP response return.
- **Verification:**
  - In `test_spof_recovery.py`, a simulated central server crash during mid-flight navigation verifies that the AMR fleet continues communicating, arbitrating, and reaching destinations with **zero collisions**. When FastAPI is restarted, pending jobs are replayed and re-queued.

### Req 2: Real AI/ML Priority Arbitration (Pure NumPy Edge Inference)
- **PS Clause:** *"Intelligent dynamic priority assignment using AI/ML techniques for right-of-way resolution at intersections and narrow corridors."*
- **Implementation:**
  - A Graph Convolutional Network (GCN) with 2 message-passing layers and a 2-layer MLP scoring head was trained offline using PyTorch (`priority_gnn_train.py`) on 5,000 synthetic warehouse conflict graphs.
  - Weights were extracted into `backend/backend/app/ml/priority_gnn_weights.npz` (8,897 float32 parameters, 36 KB file size).
  - Production edge inference is implemented in pure vectorized NumPy (`priority_gnn_infer.py`) with zero PyTorch or CUDA dependencies.
  - Safe priority bounds: Clamped strictly to `[-200.0, +200.0]`, deterministic heuristic fallback, and scanning/audit robots guaranteed lowest priority floor (`≤ -1000.0`).
- **Verification:**
  - Verified in `test_priority_fallback.py` (7/7 tests passing) ensuring parity between PyTorch reference and NumPy edge implementation, fallback activation, and bounds enforcement.

### Req 3 & 6: Zero Collisions and ≥ 20% Speedup Over Stop-and-Wait
- **PS Clause:** *"Zero inter-robot collisions and a minimum 20% reduction in total task completion time compared to traditional stop-and-wait methods when handling overlapping paths."*
- **Implementation:**
  - Stop-and-Wait Baseline (`backend/backend/app/services/policies/stop_and_wait.py`): Textbook industrial baseline where robots halt upon detecting an occupied target cell and deadlock in corridors until timeout.
  - Decentralized Policy (`robot_node.py`): Space-Time A* + GNN Priority Arbitration + Winner Bypass + Lateral Nook Stepping.
- **Empirical Results Across 30 Seeds (90 Total Simulations):**
  - **Fleet Size 5 AMRs:** Decentralized **55.17 ticks** vs Baseline **150.00 ticks** $\to$ **63.22% completion time reduction** ($p = 1.15 \times 10^{-31}$).
  - **Fleet Size 10 AMRs:** Decentralized **50.50 ticks** vs Baseline **150.00 ticks** $\to$ **66.33% completion time reduction** ($p = 1.78 \times 10^{-30}$).
  - **Fleet Size 15 AMRs:** Decentralized **62.93 ticks** vs Baseline **150.00 ticks** $\to$ **58.04% completion time reduction** ($p = 1.28 \times 10^{-31}$).
  - **Collisions:** **0 collisions** in Decentralized across all 90 runs; **0 collisions** in Baseline across all 90 runs.

### Req 4: Decentralized Task Allocation (Contract-Net Bidding)
- **PS Clause:** *"Dynamic task allocation among multiple AMRs without centralized scheduling."*
- **Implementation:**
  - Contract-Net Bidding Protocol implemented in `task_manager.py` and `robot_node.py`:
    1. Task announcement broadcasted over UDP.
    2. Idle AMRs compute deterministic bid: $Score = \frac{1000}{Dist + 1} + 2 \times Battery + 5 \times Urgency$.
    3. Winner determined deterministically; tie-breaking via lexicographical robot ID.
- **Verification:**
  - Verified in `test_decentralized_task_allocation.py` (4/4 tests passing).

### Req 5: Edge Hardware Validation (Raspberry Pi 4 & Jetson Nano)
- **PS Clause:** *"The coordination software must be suitable for edge execution on low-cost onboard computers."*
- **Implementation:**
  - `backend/Dockerfile.edge`: Standalone ARM64 container with CI check verifying absence of PyTorch/TensorFlow.
  - `scripts/emulate_edge_constraints.py`: Restricts execution to 4 logical cores (`psutil.cpu_affinity`) simulating Cortex-A72 SoC.
  - `docs/system-design/06-edge-deployment-guide.md`: Complete physical installation, systemd service unit (`amr-node.service`), and network setup instructions.
- **Measured Edge Resource Profile (500 Ticks, 10 AMRs):**
  - **Peak Fleet RSS Memory:** **135.97 MB** (Budget: < 2,000 MB) $\to$ **13.6 MB per AMR**.
  - **P95 Step Latency:** **0.867 ms** (Budget: < 50.0 ms for 20 Hz loop).
  - **Mean Step Latency:** **0.539 ms** (Budget: < 20.0 ms for 50 Hz loop).
  - **Continuous Tasks Serviced:** 80 missions completed in 500 ticks with 0 collisions.
