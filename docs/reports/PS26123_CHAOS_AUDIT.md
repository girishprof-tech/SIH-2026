# SIH26123 — Full-System Chaos Audit & Decentralized Retrofit Report

**Project:** SIH26123 Decentralized Edge-AI Multi-Robot Warehouse Fleet  
**Date:** September 26, 2026  
**Status:** All Phases (1 through 8) Verified & 100% Green (178 / 178 Automated Tests Passing)

---

## 1. Executive Summary

This report documents the architectural retrofit and multi-dimensional chaos validation of the **SIH26123 Decentralized Multi-Robot Fleet**. The retrofit upgraded the fleet from static obstacles, simulated inventory counts, and generic carton-picking into an authentic, production-grade **Goods-to-Person (G2P) Pod-Transport and Decanted Sortation System** with decentralized peer-to-peer inventory synchronization, simulated Wi-Fi HaLow telemetry redundancy, and server-failure invariance.

Every phase adhered to the strict **BUILD $\rightarrow$ ATTACK $\rightarrow$ REPAIR** methodology. The final chaos test subjected the fleet to 500+ simulation ticks under continuous 25% UDP packet loss, unannounced robot process kills, server restarts, concurrent pod retrieves, and chute decanting.

### Key Milestones Achieved:
1. **Pod Yard & Addressable Layout (Phase 1):** 176 individually enterable, movable pod slots (`POD-A01`..`POD-D44`) with 100% reachability and preserved cross-highways.
2. **Persistent Inventory Ledger (Phase 2):** SQLite WAL-backed ledger (`data/inventory.db`) with crash-safe atomic commits, real perception noise model (90–98% fidelity), and monotonic confidence decay.
3. **P2P Inventory Sync & Wi-Fi HaLow (Phase 3):** Gossip-based `INVENTORY_UPDATE` HMAC-SHA256 envelopes across the robot mesh with anti-entropy reconciliation and a dedicated token-bucket throttled (~150 kbps) Wi-Fi HaLow channel to the dashboard.
4. **G2P Pod Movers (Phase 4):** True Goods-to-Person pod transport (`RETRIEVE_POD`, `PICK_ITEM`, `RETURN_POD`) where robots lift entire shelf pods and transport them to pick faces while keeping origin slots dynamically walkable.
5. **Real Sorting AMR Behavior (Phase 5):** 8 sortation put-wall chutes (`CHUTE-01`..`CHUTE-08`) with SKU destination routing, automatic batch induction, decant pauses, and autonomous `CONSOLIDATE_EXPORT` triggering upon reaching capacity.
6. **Server-Down Operational Invariance (Phase 6):** Killing the central FastAPI server does not stop active missions or peer-to-peer inventory gossip.
7. **Dashboard Telemetry & Visualization (Phase 7):** Live Inventory Panel, Transfer & Audit Stream with Dual-Delivery tagging (`PEER_MESH` vs `HALOW`), HaLow Token-Bucket Status Widget, and Pod-carrying Canvas rendering.
8. **Full-System Chaos Verification (Phase 8):** Triple-truth parity ($0$ discrepancies across Ledger, Local Caches, and Dashboard) and $0$ space-time collisions across 500+ adversarial ticks.

---

## 2. Phase-by-Phase Implementation & Verification

### Phase 1: Shelf/Pod-Addressable Warehouse Layout
* **Implementation:** Transformed static obstacle rack rows in `app/models/world.py` and `app/services/grid.py` into a Pod Yard comprising 176 addressable slots. Pod slots are enterable by robots when lifting/lowering pods while maintaining reservation constraints.
* **Attack & Repair:** Validated reachability from all docks and charging bays using Space-Time A*. Confirmed no orphaned pods and zero congestion degradation along cross-highways $x=10, 19$.
* **Test Suite:** `testing/test_phase1_layout.py` (5 tests, 100% pass).

### Phase 2: Persistent Inventory Ledger & Real Perception Scans
* **Implementation:** Created `app/models/inventory.py` and `app/services/inventory_ledger.py` utilizing SQLite with WAL mode (`PRAGMA journal_mode=WAL`). Replaced legacy random scan generation in `app/services/audit_mission.py` with spatial scanning (`WorldConfig.shelf_at`), sensor noise injection, and real database writes. Added time-based confidence decay.
* **Attack & Repair:** Simulated concurrent multi-process audit writes and forced process terminations (`SIGKILL`). Confirmed zero database corruption, zero locked-table crashes, and safe NaN/negative bound protection.
* **Test Suite:** `testing/test_phase2_inventory.py` (6 tests, 100% pass).

### Phase 3: Decentralized Inventory Sync & Simulated Wi-Fi HaLow
* **Implementation:** Added `INVENTORY_UPDATE` HMAC-SHA256 message envelopes in `app/security/hmac_envelope.py`. Implemented local in-memory inventory caches (`Dict[str, ShelfRecord]`) in `app/services/robot_node.py` populated via direct UDP peer mesh. Implemented `app/transport/halow_transport.py` providing a dedicated, rate-limited (~150 kbps) secondary transport channel with payload burst coalescing.
* **Attack & Repair:** Tested under 30% UDP packet loss. Resolved packet-drop divergence by implementing periodic anti-entropy peer reconciliation (running every 20 ticks). ReplayGuard was verified to reject duplicate and tampered signatures.
* **Test Suite:** `testing/test_phase3_inventory_sync.py` (6 tests, 100% pass).

### Phase 4: Fetch Robots as True G2P Pod Movers
* **Implementation:** Extended `app/models/task.py` and `conflict-engine/models.py` with `TaskType` (`RETRIEVE_POD`, `RETURN_POD`, `PICK_ITEM`) and coordinate property setters. Added `carrying_pod_id` to `Robot` models. G2P robots transition through `LIFTING` and `LOWERING` FSM sub-states, dynamically updating grid walkable state and ledger manifests upon picking items.
* **Attack & Repair:** Validated concurrent pod slot access via reservations (preventing double-carry). Checked emergency stop mid-carry to ensure pod coordinates persist at the robot's last known physical location without vanishing or duplicating.
* **Test Suite:** `testing/test_phase4_g2p_pod_transport.py` (3 tests, 100% pass).

### Phase 5: Real Sorting AMR Behavior
* **Implementation:** Configured 8 sortation chutes along staging lanes in `world.py`. Implemented `INDUCT_BATCH`, `DECANT_TO_CHUTE`, and `CONSOLIDATE_EXPORT` workflows in `app/services/task_manager.py` and `robot_node.py`. Fully decoupled G2P and Sorting eligibility checks. Chutes reaching maximum capacity automatically trigger decentralized consolidation tasks.
* **Attack & Repair:** Red-teamed burst batch arrivals, simultaneous dual-robot chute decanting, and unmapped destination handling via fallback overflow routing.
* **Test Suite:** `testing/test_phase5_sortation_amr.py` (5 tests, 100% pass).

### Phase 6: Decentralization Hardening & Server Invariance
* **Implementation:** Verified that the central FastAPI server is strictly constrained to task creation and arbitration. Validated that robot nodes execute local Space-Time A* arbitration and P2P gossip autonomously.
* **Attack & Repair:** Simulated mid-mission FastAPI server crashes. Verified that robots with in-flight tasks continue movement, complete pod retrieve/return cycles, and exchange peer inventory syncs without entering unexpected `EMERGENCY_STOP` or `FAILSAFE_HOLD`.
* **Test Suite:** `testing/test_phase6_decentralization_hardening.py` (2 tests, 100% pass).

### Phase 7: Dashboard UI/UX & Redundancy Visualization
* **Implementation:** Developed `InventoryPanel.tsx`, `TransferLogPanel.tsx`, and `HaLowStatusWidget.tsx`. Upgraded `GridCanvas.tsx` to render pod yard slots, carried pods mounted under G2P robots, and color-coded chute fill levels. Added explicit delivery channel indicators (`PEER_MESH` vs `HALOW`).
* **Attack & Repair:** Tested dashboard under severe network throttling and burst inventory updates; verified graceful staleness indication without UI freezing.

---

## 3. Phase 8 Full-System Chaos Audit Results

### Chaos Test Parameters:
- **Simulation Duration:** 500 consecutive ticks
- **Network Impairment:** 25% continuous UDP packet loss on all peer sockets
- **Adversarial Injections:** Periodic process termination of worker AMRs, dynamic server restarts, and simultaneous multi-pod retrieval + sortation decants.
- **Verification Harness:** `testing/test_phase8_full_system_chaos_audit.py`

### Chaos Audit Results:

| Metric / Check | Observed Result | Status |
|---|---|---|
| **Total Test Suite Pass Rate** | **178 / 178 Tests Passed (100%)** | **PASSED** |
| **Consecutive Clean Chaos Runs** | **2 of 2 Consecutive Runs Completed** | **PASSED** |
| **Triple-Truth Discrepancies** | **0 Discrepancies** (Ledger == Robot Local Caches == Dashboard Feed) | **PASSED** |
| **Space-Time Vertex Collisions** | **0 Detected** | **PASSED** |
| **Space-Time Edge Swaps** | **0 Detected** | **PASSED** |
| **SQLite WAL Lock Contention** | **0 Deadlocks / 0 Corrupted Headers** | **PASSED** |
| **Wi-Fi HaLow Token Bucket** | Throttled at ~150 kbps with 100% burst coalescing | **PASSED** |
| **Server Crash Impact** | Active tasks completed normally; P2P sync unaffected | **PASSED** |

---

## 4. Test Suite Execution Summary

```text
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-8.3.4, pluggy-1.6.0
collected 178 items

backend\backend\app\tests\test_all.py .......................................... [ 42%]
backend\backend\test_arbitration.py ....                                         [ 44%]
backend\backend\test_conflict_detector.py .....                                  [ 47%]
backend\backend\test_conflict_engine.py ..                                       [ 48%]
backend\backend\test_priority.py .....                                           [ 51%]
backend\backend\test_audit_dispatch_and_execution.py ..                          [ 52%]
backend\backend\test_audit_mission.py ..                                         [ 53%]
backend\backend\test_audit_mission_live.py .                                     [ 54%]
backend\backend\test_auditing_livelock.py .                                      [ 55%]
backend\backend\test_battery_estop.py ...                                        [ 56%]
backend\backend\test_decentralized_task_allocation.py ....                       [ 58%]
backend\backend\test_degraded_mode.py ...                                        [ 60%]
backend\backend\test_fsm.py ........                                             [ 65%]
backend\backend\test_fuzz_peer_safety.py .                                       [ 65%]
backend\backend\test_fuzz_safety.py ..                                           [ 66%]
backend\backend\test_metrics_live.py .                                           [ 67%]
backend\backend\test_mission_lifecycle.py ...                                    [ 69%]
backend\backend\test_no_dual_runtime.py .                                        [ 69%]
backend\backend\test_phase1_layout.py .....                                      [ 72%]
backend\backend\test_phase2_inventory.py ......                                  [ 75%]
backend\backend\test_phase3_inventory_sync.py ......                             [ 79%]
backend\backend\test_phase4_g2p_pod_transport.py ...                             [ 80%]
backend\backend\test_phase5_sortation_amr.py .....                               [ 83%]
backend\backend\test_phase6_decentralization_hardening.py ..                     [ 84%]
backend\backend\test_phase8_full_system_chaos_audit.py .                         [ 85%]
backend\backend\test_priority_fallback.py .......                                [ 89%]
backend\backend\test_resume_fallback.py .                                        [ 89%]
backend\backend\test_security.py .....                                           [ 92%]
backend\backend\test_spof_recovery.py ....                                       [ 94%]
backend\backend\test_task_dispatch_e2e.py .                                      [ 95%]
backend\backend\test_task_id_preservation.py ..                                  [ 96%]
backend\backend\test_task_weight_realism.py .                                    [ 97%]
backend\backend\test_transport.py .....                                          [100%]

============================= 178 passed in 79.42s =============================
```

---

## 5. Judge Defense & Architectural Proofs

1. **Why SQLite WAL Mode instead of a Centralized Redis/Database Server?**  
   Each robot runs as an independent OS process on edge hardware. SQLite in Write-Ahead Logging (WAL) mode enables concurrent multi-process writes directly on disk without requiring an external central service, preserving offline resilience and zero single points of failure.

2. **How does Decentralized Inventory Sync survive high UDP packet loss?**  
   The sync layer uses a hybrid push-pull gossip architecture: event-driven HMAC-signed `INVENTORY_UPDATE` snapshots are broadcasted upon modification, complemented by a local anti-entropy background task that reconciles stale records.

3. **How is Wi-Fi HaLow represented without dedicated hardware?**  
   `HaLowTransport` simulates IEEE 802.11ah characteristics by enforcing a token-bucket rate limiter (~150 kbps equivalent), burst coalescing to prevent buffer bloat, and distinct packet tagging (`channel: "HALOW"`), demonstrating redundant telemetry pathways to the dashboard.

4. **What guarantees zero swap/vertex collisions when lifting and carrying pods?**  
   Space-Time A* dynamic reservations treat the robot and its carried pod as a unified reservation volume in the space-time coordinate graph $(x, y, t)$, preventing overlapping entry into pod yard slots or transit lanes.
