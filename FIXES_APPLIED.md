# Comprehensive Fixes and Verification Report

This document details the root causes, code modifications, exact verification commands, and literal terminal outputs for all five phases of the decentralized multi-robot safety and networking overhaul.

---

## Mandatory First Step: Bug Analysis
- Documented findings in [PEER_BUG_ANALYSIS.md](file:///c:/Users/STAR/Desktop/SIH-2026/PEER_BUG_ANALYSIS.md).
- Root causes identified:
  1. **Asymmetric conflict evaluation:** Only the higher-priority robot evaluated conflicts, while the lower-priority peer never evaluated the conflict on its side.
  2. **1-tick communication lag & premature drain:** Robot nodes drained their inboxes before their peer broadcast its intended move for the tick, causing moves to commit without peer coordination.
  3. **Physical clearance violation (Swap Conflict):** When robot A won arbitration and moved into robot B's current cell while robot B yielded, robot B remained in its current cell for at least 1 tick (to hold or turn). Because robot A immediately stepped into B's cell in that same tick, a physical swap collision occurred.

---

## Phase 1: Swap Conflict Bug Fix

### 1. Root Cause
1. **Pre-movement Intention Broadcast Missing:** Robots were committing moves before broadcasting and cross-verifying their intended positions for the upcoming tick.
2. **Asymmetric Arbitration:** Arbitration was only processed if one robot detected the conflict. The loser robot never symmetrically evaluated the same conflict condition.
3. **Physical Clearance Delay:** When a lower-priority robot yields, its physical chassis still occupies its current cell during tick $t+1$. If the winning robot advances into that cell on tick $t+1$, both robots occupy the same space simultaneously (a swap collision). The winner must hold for 1 tick to allow the yielding robot to clear or turn.

### 2. Files Changed
- [robot_node.py](file:///c:/Users/STAR/Desktop/SIH-2026/backend/backend/app/services/robot_node.py):
  - Pre-computed `intended_pos` before committing moves.
  - Broadcast `intended_pos` in `RESERVATION_CLAIM` payload to all peers.
  - Implemented symmetric priority arbitration: both robots independently execute identical winner/loser comparison logic (`my_score vs peer_score`, breaking ties lexicographically by `robot_id`).
  - Added physical clearance hold: if the winner intends to move into the yielding peer's current position, the winner pauses 1 tick at its current cell.
  - Added fail-safe synchronization: robots wait up to `max_peer_wait` for adjacent peers to broadcast their tick intentions before moving.
- [arbitration.py](file:///c:/Users/STAR/Desktop/SIH-2026/conflict-engine/arbitration.py):
  - Added winner pause/shift logic when the winner's next waypoint is the loser's current cell, allowing the yielding robot to turn/clear.
  - Extended hold reservations for stationary robots at their destination.
- [verify_no_swap.py](file:///c:/Users/STAR/Desktop/SIH-2026/verify_no_swap.py):
  - Created standalone multi-process log parser asserting 0 swap collisions and 0 cell collisions across all ticks for all robot processes.

### 3. Verification Command & Literal Output
Command:
```powershell
python test_decentralization.py
python verify_no_swap.py
```

Literal Terminal Output:
```
Parsed AMR-01: 65 ticks logged (ticks 0 to 64)
Parsed AMR-02: 64 ticks logged (ticks 0 to 63)
Parsed AMR-03: 64 ticks logged (ticks 0 to 63)
Parsed AMR-04: 64 ticks logged (ticks 0 to 63)
Parsed AMR-05: 63 ticks logged (ticks 0 to 62)

============================================================
VERIFICATION RESULTS
============================================================
Total ticks checked: 65
Total robots checked: 5
Swap collisions found: 0
Cell collisions found: 0
  -> PASSED: Zero swap collisions detected across all ticks!
  -> PASSED: Zero vertex/cell collisions detected across all ticks!
============================================================
OVERALL VERIFICATION: SUCCESS (100% Collision-Free)
```

Both robots (`AMR-01` and `AMR-02`, as well as `AMR-03` and `AMR-04`) explicitly logged conflict detection:
- `AMR-01` logged: `ARBITRATION RESULT: WON against AMR-02. Action=PROCEED.`
- `AMR-02` logged: `ARBITRATION RESULT: LOST to AMR-01. Action=YIELD. Yielded right-of-way, incremented wait_ticks=1.`

---

## Phase 2: UDP Socket Networking Transport

### 1. Root Cause
The robots originally communicated using Python's `multiprocessing.Queue`. `multiprocessing.Queue` is an in-memory Python IPC mechanism that only functions when all worker processes share a common parent process on a single machine. It cannot communicate across physical network interfaces or separate hardware.

### 2. Files Changed
- [robot_node.py](file:///c:/Users/STAR/Desktop/SIH-2026/backend/backend/app/services/robot_node.py):
  - Replaced `inbox: mp.Queue` and `peer_mailboxes: Dict[str, mp.Queue]` with a real UDP socket:
    ```python
    self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.sock.bind((self.host, self.port))
    self.sock.setblocking(False)
    ```
  - Replaced `mailbox.put_nowait(...)` with JSON serialization and non-blocking `sock.sendto(data, (self.host, peer_port))`.
  - Replaced `inbox.get_nowait(...)` with non-blocking `sock.recvfrom(4096)` loop catching `BlockingIOError`.
  - Added module docstring explaining UDP loopback and LAN compatibility.
- [fleet_orchestrator.py](file:///c:/Users/STAR/Desktop/SIH-2026/backend/backend/app/services/fleet_orchestrator.py):
  - Replaced peer mailbox creation (`mp.Queue`) with a port map: `peer_ports = {rid: 9000 + i for i, rid in enumerate(robot_ids)}`.
  - Passed `peer_ports` dictionary to each spawned robot process.

### 3. Verification Command & Literal Output
Command:
```powershell
python test_decentralization.py
```

Literal Terminal Output:
```
[PHASE 1] Initializing Decentralized Multi-Process Fleet Orchestrator...
  -> Spawning 5 independent robot OS processes (AMR-01 .. AMR-05)...
[FleetOrchestrator] Robot AMR-01 spawned (PID 26732)
[FleetOrchestrator] Robot AMR-02 spawned (PID 14272)
[FleetOrchestrator] Robot AMR-03 spawned (PID 21104)
[FleetOrchestrator] Robot AMR-04 spawned (PID 19164)
[FleetOrchestrator] Robot AMR-05 spawned (PID 4476)
  -> Confirmed: 5 independent processes running concurrently.

[PHASE 2] Starting Centralized FastAPI Telemetry Viewer...
  -> FastAPI PID = 13808
  -> Waiting 5.0 seconds for initial simulation ticks...

[PHASE 3] Simulating Hard Crash: KILLING Centralized FastAPI Server...
  -> Centralized FastAPI server (PID 13808) KILLED.
  -> Verifying robot processes continue running autonomously for 8.0s...
  -> Confirmed: 5/5 robot processes are still running smoothly after server death!

[PHASE 4] Inspecting Robot Peer-to-Peer Logs during Server Downtime...
  -> Confirmed: Peer-to-peer conflict detection and arbitration occurred autonomously!

[PHASE 5] Restarting FastAPI Telemetry Viewer...
  -> New FastAPI PID = 28916
  -> Confirmed: Restarted FastAPI reconnected seamlessly at Tick 63!

[PHASE 6] Shutting down demo cleanly...
  -> All processes terminating cleanly.

================================================================================
RESULT: DECENTRALIZATION PROOF FULLY SUCCESSFUL!
Robots run in independent OS processes, negotiate peer-to-peer,
and operate completely unhindered when the central backend server dies.
================================================================================
```

And subsequent swap verification:
```powershell
python verify_no_swap.py
```
Output:
```
Parsed AMR-01: 65 ticks logged (ticks 0 to 64)
Parsed AMR-02: 64 ticks logged (ticks 0 to 63)
Parsed AMR-03: 64 ticks logged (ticks 0 to 63)
Parsed AMR-04: 64 ticks logged (ticks 0 to 63)
Parsed AMR-05: 63 ticks logged (ticks 0 to 62)

============================================================
VERIFICATION RESULTS
============================================================
Total ticks checked: 65
Total robots checked: 5
Swap collisions found: 0
Cell collisions found: 0
  -> PASSED: Zero swap collisions detected across all ticks!
  -> PASSED: Zero vertex/cell collisions detected across all ticks!
============================================================
OVERALL VERIFICATION: SUCCESS (100% Collision-Free)
```

---

## Phase 3: Decentralized Peer Safety Fuzz Testing

### 1. Root Cause
- The existing fuzz test suite (`test_fuzz_safety.py`) only tested the centralized conflict engine (`detect_conflicts` / `resolve_conflict`), never testing the decentralized peer functions (`detect_peer_conflict` / `resolve_peer_conflict`). Consequently, peer swap edge cases went undetected despite 500 passing centralized tests.
- In `conflict-engine/arbitration.py`, `current_tick = conflict.get("current_tick")` was followed by `if current_tick <= 0: current_tick = max(0, conflict.get("tick", 1) - 1)`. When `current_tick == 0` (the first simulation tick), it incorrectly treated `0` as falsy/invalid, offsetting `current_tick` by `target_tick - 1` and causing future path timestamps to desynchronize from the simulation clock.

### 2. Files Changed
- [test_fuzz_peer_safety.py](file:///c:/Users/STAR/Desktop/SIH-2026/test_fuzz_peer_safety.py):
  - Created new Hypothesis test harness configured with `@settings(max_examples=300, deadline=None)`.
  - Designed scenario generator biased heavily toward collision courses: head-on corridor segments, perpendicular 4-way intersection crossings, and multi-robot clusters.
  - Directly imported and invoked `detect_peer_conflict` and `resolve_peer_conflict`.
  - Asserted invariant 1 (no two robots ever share a cell at the same tick) and invariant 2 (no two robots ever swap cells in a single tick).
- [arbitration.py](file:///c:/Users/STAR/Desktop/SIH-2026/conflict-engine/arbitration.py):
  - Fixed `current_tick` extraction to check `if current_tick is None:` rather than `if current_tick <= 0:`.
  - Implemented strictly sequential timestamp generation when shifting the winner's path during yield pauses.

### 3. Verification Command & Literal Output
Command:
```powershell
python -m pytest test_fuzz_peer_safety.py -v
```

Literal Terminal Output:
```
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\STAR\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
cachedir: .pytest_cache
hypothesis profile 'default'
rootdir: C:\Users\STAR\Desktop\SIH-2026
plugins: anyio-4.15.0, hypothesis-6.167.1, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

test_fuzz_peer_safety.py::test_fuzz_peer_safety PASSED                   [100%]

============================= 1 passed in 15.62s ==============================
```

Regression check on centralized fuzz test suite (`test_fuzz_safety.py`):
```powershell
python -m pytest test_fuzz_safety.py -v
```

Literal Terminal Output:
```
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.1.1, pluggy-1.6.0 -- C:\Users\STAR\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
cachedir: .pytest_cache
hypothesis profile 'default'
rootdir: C:\Users\STAR\Desktop\SIH-2026
plugins: anyio-4.15.0, hypothesis-6.167.1, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 2 items

test_fuzz_safety.py::test_no_two_robots_ever_share_a_cell PASSED         [ 50%]
test_fuzz_safety.py::test_no_robot_starves PASSED                        [100%]

============================= 2 passed in 27.12s ==============================
```

---

## Phase 4: Removal of Stale Fake Files

### 1. Root Cause
Three standalone HTML files (`pathfinding/warehouse_simulation.html`, `pathfinding/warehouse_simulator.html`, `unified_warehouse_simulator.html`) contained pre-baked, hardcoded static JSON animation data completely disconnected from the actual backend, pathfinding algorithms, and conflict resolution engines.

### 2. Files Removed
- `pathfinding/warehouse_simulation.html` (deleted)
- `pathfinding/warehouse_simulator.html` (deleted)
- `unified_warehouse_simulator.html` (deleted)

### 3. Verification Command & Literal Output
Command:
```powershell
Test-Path pathfinding/warehouse_simulation.html, pathfinding/warehouse_simulator.html, unified_warehouse_simulator.html
git status -s
```

Literal Terminal Output:
```
False
False
False
```
```
 D pathfinding/warehouse_simulation.html
 D pathfinding/warehouse_simulator.html
 D unified_warehouse_simulator.html
```

---

## Known Limitations
1. **Loopback vs. Physical LAN Testing:** The UDP socket implementation currently operates on the loopback interface (`127.0.0.1`) across independent OS processes on a single Windows machine. While standard UDP sockets are used (making the codebase directly transportable to multiple physical machines on a local subnet), physical multi-machine LAN characteristics (such as packet loss, radio interference, and high network jitter) have not yet been evaluated on physical embedded AMR compute hardware.
2. **Fixed Port Allocation:** Ports are currently derived statically as `9000 + N` for demonstration purposes. In production deployments with dynamic DHCP IP assignments or multiple AMR instances per hardware host, dynamic service discovery (e.g. mDNS/Zeroconf) or a configuration file should be used.
