# SIH-2026 Test Suite Directory

This directory contains the automated end-to-end, integration, security, decentralization, and safety regression test suites for the decentralized Edge-AI multi-robot warehouse coordination system.

---

## Running Test Suites

All tests resolve project roots via `Path(__file__).resolve().parents[1]` and can be executed from the project root or within `testing/`.

### 1. Run All Tests via Pytest
```bash
# Run all integration, safety, and model tests in this directory
python -m pytest testing/ -v

# Run fast unit and integration tests (excluding long fuzzing/concurrency tests)
python -m pytest testing/test_fsm.py testing/test_security.py testing/test_battery_estop.py testing/test_audit_mission.py -v
```

### 2. Standalone Multi-Process Resilience & Decentralization Tests
```bash
# Decentralization proof: spawns 5 independent robot processes, kills FastAPI server, verifies continued operations & reconnect
python testing/test_decentralization.py

# End-to-end WebSocket streaming and fleet orchestrator live verification
python testing/test_e2e_fleet_websocket.py

# Single Point of Failure (SPOF) recovery proof
python testing/test_spof_recovery.py
```

### 3. Safety Invariant & Fuzzing Tests
```bash
# Peer-to-peer collision and space-time reservation safety fuzzing
python -m pytest testing/test_fuzz_peer_safety.py -v

# Hypothesis property-based fuzzing test
python -m pytest testing/test_fuzz_safety.py -v

# 50-scenario full integration stress test
python testing/full_integration_test.py
```

### 4. Post-Execution Trajectory Log Verification
```bash
# Parse logs/robot_*.log to verify strictly 0 vertex collisions and 0 swap collisions across entire runtime
python testing/verify_no_swap.py
```

---

## Test Inventory

| File | Type | Description |
|---|---|---|
| `test_decentralization.py` | Multi-Process | Proves AMRs operate without central coordinator; tests server crash resilience |
| `test_decentralized_task_allocation.py` | Integration | Validates decentralized Contract-Net bidding protocol & tie-breaking |
| `test_fsm.py` | State Machine | Tests 11 FSM states, pre-conflict state restoration, and transition guards |
| `test_security.py` | Network | Validates HMAC-SHA256 signature verification & ReplayGuard nonce deduplication |
| `test_battery_estop.py` | Safety | Validates emergency stop, battery threshold triggers, and supervisor reset |
| `test_audit_mission.py` | Integration | Validates inventory scanning audit patrol logic |
| `test_audit_mission_live.py` | Live E2E | Tests audit dispatch via REST API to live decentralized fleet |
| `test_auditing_livelock.py` | Safety | Verifies audit AMRs yield and break livelock / stalls |
| `test_priority_fallback.py` | AI / ML | Tests GNN priority inference, clamping, and analytic formula fallback |
| `test_resume_fallback.py` | State Machine | Tests deterministic conflict resume events (`RESUME_PICKUP`, `RESUME_DROPOFF`) |
| `test_task_id_preservation.py` | Invariant | Ensures active task IDs remain preserved across conflict yield / detours |
| `test_task_weight_realism.py` | Physical | Tests physical battery drain and friction adjustments based on payload mass |
| `test_transport.py` | Network | Tests loopback and non-blocking UDP socket communications |
| `test_e2e_fleet_websocket.py` | End-to-End | Verifies live WebSocket `TICK_UPDATE` streaming to frontend |
| `test_no_dual_runtime.py` | Architectural | Proves only one simulation runtime exists in the active application |
| `test_degraded_mode.py` | Network | Validates AMR performance under synthetic UDP packet loss |
| `test_spof_recovery.py` | Resilience | Tests fleet survival when individual nodes fail or restart |
| `test_metrics_live.py` | Telemetry | Validates real-time aggregation of planner latency and active conflicts |
| `test_task_dispatch_e2e.py` | Dispatch | Validates REST `/api/job` end-to-end task ingestion and assignment |
| `test_fuzz_peer_safety.py` | Fuzzing | Generates high-density random paths to verify zero collision arbitration |
| `test_fuzz_safety.py` | Hypothesis | Hypothesis-driven property testing of spatial reservations |
| `full_integration_test.py` | Stress Harness | Runs 50 multi-robot scenarios (N=3,5,10,20) asserting all invariants |
| `debug_seed_1004.py` | Debug | Scenario debugger for Seed 1004 with 20 AMRs |
| `verify_no_swap.py` | Log Validator | Checks runtime `logs/robot_*.log` for physical cell overlaps or swaps |
