# PEER_BUG_ANALYSIS: Decentralized Peer Conflict & Swap Collision Root Cause Analysis

## Executive Summary

In the decentralized multi-robot architecture (`robot_node.py`, `fleet_orchestrator.py`), each AMR executes as an independent operating system process (`multiprocessing.Process`). In a head-on encounter (such as AMR-01 at (9, 6) moving east to (10, 6), and AMR-02 at (10, 6) moving west to (9, 6) at tick 8), a critical physical collision occurs where both robots occupy cell (10, 6) simultaneously.

---

## 1. How a Robot Currently Decides to Move Each Tick

In `RobotNode.step(tick)`:
1. **Priority Update**: Updates `self.robot.priority_score` via `calculate_priority_score(self.robot, self.task, dist)`.
2. **Broadcast**: Puts a `RESERVATION_CLAIM` into each peer's `multiprocessing.Queue` containing:
   `{"position": self.robot.position, "path": self.robot.path[:6], ...}`.
3. **Drain Inbox**: Non-blocking `get_nowait()` drains messages currently in `self.inbox` and updates `self.peers[peer_id]`.
4. **Proximity Filter**: Filters peers where Manhattan distance $\le 2$ and `last_seen_tick >= tick - 2`.
5. **Conflict Check & Arbitration**:
   - Loops over `nearby_peers`, creates `peer_proxy = Robot(...)`.
   - Calls `conflict = detect_peer_conflict(self.robot, peer_proxy, tick)`.
   - If a conflict is found, calls `resolve_peer_conflict(...)`.
   - If `resolution["loser_id"] == self.robot.robot_id`, sets `action_taken = "YIELDED / BRAKED"`.
6. **Move Execution**:
   - If `action_taken == "YIELDED / BRAKED"`, holds position for this tick.
   - Else if `len(self.robot.path) > 1 and self.robot.path[1]["t"] == tick + 1`, updates `self.robot.position = next_pos` and pops `path[0]`.
7. **Telemetry & Log**: Writes state to `logs/robot_{robot_id}.log`.

---

## 2. Root Cause Analysis: Why the Loser Never Yields (or Yields Too Late / Incorrectly)

### Root Cause A: 1-Tick Asynchronous Mailbox Lag
Because the robot processes run concurrently on separate OS processes without a barrier:
- **Robot A (AMR-01) enters `step(8)` first**:
  - AMR-01 is at `(9, 6)`. Its next waypoint is `(10, 6)`.
  - AMR-01 sends its tick 8 claim to AMR-02's inbox.
  - AMR-01 drains its own inbox. But AMR-02 has **not yet executed tick 8**!
  - Therefore, AMR-01 only has AMR-02's claim from **tick 7**, when AMR-02 was at `(11, 6)`.
  - AMR-01 evaluates `detect_peer_conflict(AMR-01 at (9, 6), AMR-02 at (11, 6), tick=8)`.
  - For a swap conflict, `detect_conflicts` requires:
    `next_a == pos_b and next_b == pos_a`.
    Here, `next_a = (10, 6)`, but `pos_b = (11, 6)`.
    `next_a != pos_b` $\rightarrow$ **NO SWAP CONFLICT DETECTED BY AMR-01**.
  - Cell overlap at tick 9: `p_a = (10, 6)`, `p_b = (9, 6)` $\rightarrow$ **NO OVERLAP DETECTED**.
  - AMR-01 concludes the path is clear, commits its move to `(10, 6)`, and logs `Action=MOVED`.

- **Robot B (AMR-02) enters `step(8)` second**:
  - AMR-02 is at `(10, 6)`. Its next waypoint is `(9, 6)`.
  - AMR-02 drains its inbox and receives AMR-01's fresh tick 8 claim (`Pos=(9, 6)`, `next=(10, 6)`).
  - AMR-02 checks `detect_peer_conflict(AMR-02 at (10, 6), AMR-01 at (9, 6), tick=8)`.
  - `next_b = (9, 6) == pos_a (9, 6)` AND `next_a = (10, 6) == pos_b (10, 6)`!
  - AMR-02 detects `SWAP_CONFLICT`!
  - AMR-02 calls `resolve_peer_conflict()`. Priority 491 (AMR-01) > Priority 291 (AMR-02).
  - AMR-02 realizes it is the **loser**.
  - AMR-02 logs: `ARBITRATION RESULT: LOST to AMR-01. Action=YIELD.`
  - AMR-02 sets `action_taken = "YIELDED / BRAKED"` and holds position at `(10, 6)`.

### Root Cause B: Flawed Yield Semantics (Holding in the Trajectory of Oncoming Winner)
Even though AMR-02 detected the swap and yielded:
- What did yielding mean in `step()`?
  ```python
  elif action_taken == "YIELDED / BRAKED":
      # Robot yielded: hold position for this tick
      pass
  ```
- Holding position means AMR-02 **remains at (10, 6)**!
- But `(10, 6)` is the **exact cell AMR-01 was moving into**!
- AMR-01 moved into `(10, 6)` because it never detected the conflict due to the 1-tick lag.
- AMR-02 stayed at `(10, 6)` because it yielded.
- **Both robots occupy (10, 6) at tick 8!**

### Root Cause C: Unilateral Intention Commitment without 2-Phase Protocol
A robot updates `self.robot.position` unilaterally in the same pass that it broadcasts. There was no 2-phase exchange:
1. Phase A: Compute & broadcast INTENDED next position.
2. Phase B: Receive all peer intended next positions, check for vertex and swap conflicts symmetrically, and only commit if no unresolved conflict exists.

---

## 3. Required Solution Architecture (Phase 1)

1. **Intended Position Separation**:
   Before updating position, every robot must compute its candidate `intended_pos` for `tick + 1`.
2. **Symmetric Intended Move Broadcast**:
   Broadcast `intended_next_pos` and `current_pos`.
3. **Deterministic Symmetric Evaluation**:
   Both robots evaluate the exact same condition:
   - Does `my_intended == peer_intended`? (Vertex conflict)
   - Does `my_intended == peer_current and peer_intended == my_current`? (Swap conflict)
   - Does `my_intended == peer_current` where peer is waiting/stationary? (Stationary blockage)
4. **Synchronized Loser Yield**:
   - The winner proceeds to `intended_pos`.
   - The loser CANNOT move into `intended_pos`, AND if it is a swap conflict, the loser must either wait BEFORE the intersection or step aside/replan. When AMR-01 and AMR-02 are at (9, 6) and (10, 6), they are adjacent! If AMR-01 moves to (10, 6), AMR-02 CANNOT stay at (10, 6).
   - Therefore, conflict detection must happen **BEFORE** the robots become adjacent or, when adjacent, the loser must yield/brake before stepping into the contested cell, while the winner is granted right-of-way only when the cell is vacated or the winner waits if the cell is still occupied!
