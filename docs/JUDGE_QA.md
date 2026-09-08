# SIH26123: Judge Q&A & Technical Defense Guide

### Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses
**Organization:** Bharat Electronics Limited (BEL) | Smart India Hackathon 2026  
**Problem Statement ID:** `SIH26123`  

This document equips the team with precise, technical, and data-backed answers to the most critical architecture and verification questions asked by technical judges.

---

### Q1: Where is the AI/ML model actually running, and what role does it play?
**Answer:**  
Our AI model is an onboard **Graph Neural Network (GNN)** that predicts dynamic priority scores during right-of-way arbitration at narrow warehouse bottlenecks.
- **Training (Offline):** A 2-layer Graph Convolutional Network (GCN) with a 2-layer MLP scoring head was trained on 5,000 synthetic warehouse conflict graphs (`data/priority_training_dataset.npz`) using PyTorch (`backend/backend/app/ml/priority_gnn_train.py`).
- **Inference (Edge Onboard):** The trained weights (8,897 float32 parameters, 36 KB) were extracted to `priority_gnn_weights.npz`. Production inference on each AMR node is implemented in **pure vectorized NumPy** (`priority_gnn_infer.py`) with **zero PyTorch or CUDA dependencies**.
- **Inference Latency:** Executes in **0.12 ms** per conflict on CPU.
- **Safety Bounds:** Model outputs are strictly bounded in `[-200.0, +200.0]` with a deterministic fallback heuristic and a hard audit floor (`≤ -1000.0`) for scanning robots.

---

### Q2: How do you prove this runs on constrained edge hardware like Raspberry Pi or Jetson Nano?
**Answer:**  
We established a strict edge validation methodology with zero heavy runtime overhead:
1. **Zero-PyTorch Architecture:** Traditional DL runtimes consume `~1.8 GB` disk and `~450 MB` idle RAM. Our pure NumPy engine eliminates this entirely.
2. **Resource Constraints Profiling (`scripts/emulate_edge_constraints.py`):** We pinned a 10-AMR fleet simulation to 4 logical CPU cores (`psutil.cpu_affinity`) to simulate a quad-core Cortex-A72 processor and profiled memory and latency across 500 active ticks:
   - **Measured Peak RSS:** **13.6 MB per AMR** (Budget: `< 250 MB` on a 2GB Raspberry Pi 4).
   - **Measured P95 Step Latency:** **0.867 ms** (Budget: `< 50.0 ms` for a 20 Hz control loop).
   - **Mean Step Latency:** **0.539 ms** (Budget: `< 20.0 ms` for 50 Hz control loop).
3. **Containerization & Deployment Guide:** `Dockerfile.edge` contains automated CI checks that immediately fail if `torch` or `tensorflow` are detected. Step-by-step systemd service configs are documented in `docs/system-design/06-edge-deployment-guide.md`.

---

### Q3: Where is the empirical proof of the ≥ 20% speedup over traditional stop-and-wait methods?
**Answer:**  
We built a rigorous, automated benchmark (`scripts/benchmark_coordination_policies.py`) executing **30 random seeds** across fleet sizes **[5, 10, 15]** on identical layouts:
- **Fleet Size 5 AMRs:** Decentralized **55.17 ticks** vs Baseline **150.00 ticks** $\to$ **63.22% completion time reduction** ($p = 1.15 \times 10^{-31}$).
- **Fleet Size 10 AMRs:** Decentralized **50.50 ticks** vs Baseline **150.00 ticks** $\to$ **66.33% completion time reduction** ($p = 1.78 \times 10^{-30}$).
- **Fleet Size 15 AMRs:** Decentralized **62.93 ticks** vs Baseline **150.00 ticks** $\to$ **58.04% completion time reduction** ($p = 1.28 \times 10^{-31}$).
- **Collision Invariant:** **Strictly zero collisions** across all 90 runs in both policies.
- **Why Stop-and-Wait Fails:** In narrow aisles, stop-and-wait AMRs halt indefinitely upon seeing an oncoming peer, triggering cascading deadlock timeouts. Our decentralized coordination proactively yields into lateral nooks or routes winner bypasses, completely eliminating stall cycles.

---

### Q4: What happens if the central server or network connection crashes?
**Answer:**  
Our system has **zero single points of failure (SPOF)**:
1. **Autonomous Execution:** The central FastAPI server is strictly a non-authoritative telemetry viewer and initial job gateway. All AMRs run as independent OS processes communicating directly over peer-to-peer UDP. If FastAPI is killed (`SIGKILL`), the fleet continues negotiating, avoiding obstacles, and finishing tasks without interruption (proven in `test_spof_recovery.py`).
2. **Write-Ahead Job Journal (`data/job_log.jsonl`):** Every incoming job is logged to an append-only journal and flushed with `os.fsync` before HTTP 200 is returned.
3. **Automatic Crash Recovery:** When FastAPI restarts, `JobJournal.recover_uncompleted_jobs()` replays the journal, reconstructing in-flight tasks and re-queueing them without duplicate dispatches or lost orders.

---

### Q5: How are tasks dynamically allocated without a central dispatcher?
**Answer:**  
We implemented a decentralized **Contract-Net Protocol** (`backend/backend/app/services/task_manager.py`):
1. **Task Announcement:** New task requirements (pickup, dropoff, urgency, payload weight) are broadcast across the peer network.
2. **Local Bid Calculation:** Each idle AMR calculates its local bid based on Euclidean distance to pickup, battery state of charge, and urgency:
   $$\text{Score} = \frac{1000}{\text{Distance} + 1} + 2 \times \text{Battery} + 5 \times \text{Urgency}$$
3. **Consensus & Tie-Breaking:** AMRs exchange bids over UDP; the highest score claims the task. Ties are broken deterministically by lexicographical robot ID (`AMR-01 < AMR-02`).
4. **Validation:** 100% test pass rate in `test_decentralized_task_allocation.py`.

---

### Q6: How do you guarantee zero collisions at intersections and narrow corridors?
**Answer:**  
Collision freedom is mathematically guaranteed through a multi-tier reservation and arbitration protocol:
1. **Space-Time Reservations:** Each AMR broadcasts a 30-tick space-time claim envelope `(x, y, t)` signed with HMAC-SHA256.
2. **Symmetric Conflict Detection:** Evaluates both vertex collisions (`pos_A(t) == pos_B(t)`) and edge swap collisions (`pos_A(t) == pos_B(t-1) \land pos_B(t) == pos_A(t-1)`).
3. **Deterministic Arbitration:** The higher-priority AMR proceeds; the lower-priority AMR replans or steps aside into an adjacent free nook.
4. **Winner Bypass Safeguard:** If a yielding peer cannot clear immediately, the winner replans around all blocked cells, checking all peer positions before advancing.

---

### Q7: How does the system handle Wi-Fi packet drops or degraded network conditions?
**Answer:**  
1. **Degraded Speed Mode:** Each AMR tracks the `last_seen_tick` of all peers. If an adjacent peer (within 3 cells) misses heartbeats, the AMR enters degraded mode, throttling its speed to 50% (moving only on alternate ticks).
2. **Failsafe Proximity Hold:** If a peer within 2 cells drops out for `> 2 ticks`, the AMR halts immediately until communication is confirmed or the path is clear.
3. **Replay Guard:** Monotonic sequence counters and HMAC-SHA256 signatures reject delayed, replayed, or spoofed packets.
4. **Live Chaos Injection:** The UI and backend include chaos testing endpoints (`/api/chaos/packet-loss`) demonstrating continuous collision freedom even under 30% packet drop rates.
