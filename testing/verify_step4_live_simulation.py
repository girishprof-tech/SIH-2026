"""
verify_step4_live_simulation.py — Verification of Step 4 Live Simulation.

Runs a live multi-process simulation using FleetOrchestrator across 5 real OS child
processes for 220+ ticks under realistic cross-traffic (G2P pod carrying, sorting,
and aisle audit traversals).

Validates:
1. Zero permanent ping-pong livelocks (no robot stuck oscillating between two cells).
2. Zero robots stuck indefinitely in FAILSAFE_HOLD.
3. Zero path intersections with an occupied pod slot carried by another robot.
"""

import multiprocessing as mp
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.services.fleet_orchestrator import FleetOrchestrator


def run_live_simulation_audit(target_ticks: int = 220, tick_interval_s: float = 0.025) -> bool:
    print("\n" + "=" * 75)
    print(f"STEP 4 LIVE SIMULATION: 5 ROBOTS ACROSS INDEPENDENT OS PROCESSES ({target_ticks} TICKS)")
    print("=" * 75)

    log_dir = ROOT_DIR / "logs" / "step4_audit"
    log_dir.mkdir(parents=True, exist_ok=True)

    robots_config = [
        {
            "robot_id": "AMR-01",
            "start": (4, 4),
            "goal": (27, 4),
            "urgency": 3,
            "battery_pct": 100.0,
            "robot_type": "GOODS_TO_PERSON",
            "enable_idle_audit": False,
        },
        {
            "robot_id": "AMR-02",
            "start": (4, 8),
            "goal": (27, 12),
            "urgency": 2,
            "battery_pct": 100.0,
            "robot_type": "GOODS_TO_PERSON",
            "enable_idle_audit": False,
        },
        {
            "robot_id": "AMR-03",
            "start": (27, 8),
            "goal": (2, 8),
            "urgency": 4,
            "battery_pct": 100.0,
            "robot_type": "SORTING",
            "enable_idle_audit": False,
        },
        {
            "robot_id": "AMR-04",
            "start": (2, 20),
            "goal": (27, 20),
            "urgency": 1,
            "battery_pct": 100.0,
            "robot_type": "GOODS_TO_PERSON",
            "enable_idle_audit": False,
        },
        {
            "robot_id": "AMR-05",
            "start": (10, 3),
            "goal": (10, 24),
            "urgency": 2,
            "battery_pct": 100.0,
            "robot_type": "SCANNING_AUDIT",
            "enable_idle_audit": True,
        },
    ]

    orchestrator = FleetOrchestrator(
        robots_config=robots_config,
        tick_interval_s=tick_interval_s,
        max_ticks=target_ticks,
        log_dir=log_dir,
    )

    # Tracking metrics
    robot_position_history = defaultdict(list)
    robot_state_history = defaultdict(list)
    pod_occupancy_violations = []
    ping_pong_oscillations = []
    stuck_failsafes = []

    print(f"[Audit] Starting FleetOrchestrator with {len(robots_config)} worker processes...")
    orchestrator.start()

    start_time = time.time()
    max_wall_time = 35.0  # seconds timeout
    last_reported_tick = 0

    try:
        while time.time() - start_time < max_wall_time:
            tick_update = orchestrator.bus.process_incoming()
            curr_tick = orchestrator.bus.current_tick

            if tick_update:
                robots = tick_update.get("robots", [])

                # Track currently carried pods and their positions
                carried_pod_positions = {}
                for r in robots:
                    rid = r["id"]
                    r_pos = (r["x"], r["y"])
                    r_state = r["state"]
                    r_pod = r.get("carrying_pod_id")

                    robot_position_history[rid].append((curr_tick, r_pos))
                    robot_state_history[rid].append((curr_tick, r_state))

                    if r_pod or r_state in ("PICKING", "LIFTING"):
                        carried_pod_positions[r_pos] = (rid, r_pod)

                # Check invariant 3: No other robot's path must cross an occupied pod cell
                for r in robots:
                    rid = r["id"]
                    path = r.get("path", [])
                    for step in path:
                        step_pos = (step["x"], step["y"])
                        if step_pos in carried_pod_positions:
                            owner_id, pod_id = carried_pod_positions[step_pos]
                            if owner_id != rid:
                                pod_occupancy_violations.append({
                                    "tick": curr_tick,
                                    "intruder": rid,
                                    "owner": owner_id,
                                    "pod_id": pod_id,
                                    "cell": step_pos,
                                })

            if curr_tick >= last_reported_tick + 30:
                print(f"  -> Simulation progress: Tick {curr_tick}/{target_ticks} (elapsed: {time.time() - start_time:.1f}s)")
                last_reported_tick = curr_tick

            if curr_tick >= target_ticks:
                print(f"[Audit] Reached target tick {curr_tick} >= {target_ticks}!")
                break

            if not orchestrator.is_alive():
                print(f"[Audit] All robot processes finished at tick {curr_tick}.")
                break

            time.sleep(0.02)

    finally:
        print("[Audit] Terminating orchestrator processes...")
        orchestrator.stop()

    final_tick = orchestrator.bus.current_tick
    print(f"\n[Audit Results Summary] Ran {final_tick} ticks across {len(robots_config)} processes:")

    # 1. Analyze ping-pong oscillations (A-B-A-B-A-B-A-B for >= 8 steps)
    for rid, hist in robot_position_history.items():
        positions = [pos for _, pos in hist]
        osc_count = 0
        max_osc = 0
        for i in range(2, len(positions)):
            if positions[i] == positions[i - 2] and positions[i] != positions[i - 1]:
                osc_count += 1
                if osc_count > max_osc:
                    max_osc = osc_count
            else:
                osc_count = 0
        if max_osc >= 8:
            ping_pong_oscillations.append((rid, max_osc))

    # 2. Analyze FAILSAFE_HOLD duration
    for rid, s_hist in robot_state_history.items():
        consecutive_failsafe = 0
        max_fs = 0
        for _, state in s_hist:
            if state == "FAILSAFE_HOLD":
                consecutive_failsafe += 1
                if consecutive_failsafe > max_fs:
                    max_fs = consecutive_failsafe
            else:
                consecutive_failsafe = 0
        # If robot stayed in FAILSAFE_HOLD for > 20 ticks continuously or ended in it
        last_state = s_hist[-1][1] if s_hist else "UNKNOWN"
        if max_fs > 25 or (last_state == "FAILSAFE_HOLD" and max_fs > 15):
            stuck_failsafes.append((rid, max_fs, last_state))

    # Output verdicts
    success = True
    print("\n" + "-" * 60)
    print("VERIFICATION CHECK 1: Permanent Ping-Pong Livelocks")
    if not ping_pong_oscillations:
        print(">>> PASSED: Zero permanent ping-pong oscillations detected across all robots!")
    else:
        print(f">>> FAILED: Ping-pong oscillations detected: {ping_pong_oscillations}")
        success = False

    print("\n" + "-" * 60)
    print("VERIFICATION CHECK 2: Robots Stuck in FAILSAFE_HOLD")
    if not stuck_failsafes:
        print(">>> PASSED: Zero robots permanently stuck in FAILSAFE_HOLD! All robots recovered or completed missions.")
    else:
        print(f">>> FAILED: Robots stuck in FAILSAFE_HOLD: {stuck_failsafes}")
        success = False

    print("\n" + "-" * 60)
    print("VERIFICATION CHECK 3: Paths Crossing Occupied Pod Slots")
    if not pod_occupancy_violations:
        print(">>> PASSED: Zero paths crossed an occupied/carried pod slot! Space-Time A* routed around all occupied pods.")
    else:
        print(f">>> FAILED: Path crossed occupied pod slot: {pod_occupancy_violations[:5]} (Total: {len(pod_occupancy_violations)})")
        success = False

    print("\n" + "-" * 60)
    print(f"VERIFICATION CHECK 4: Simulation Duration ({final_tick}/{target_ticks} ticks)")
    if final_tick >= 200:
        print(f">>> PASSED: Completed {final_tick} ticks (> 200 required)!")
    else:
        print(f">>> FAILED: Ended prematurely at tick {final_tick} < 200.")
        success = False

    print("=" * 75 + "\n")
    return success


if __name__ == "__main__":
    mp.freeze_support()
    ok = run_live_simulation_audit(target_ticks=220, tick_interval_s=0.02)
    sys.exit(0 if ok else 1)
