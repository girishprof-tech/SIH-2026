"""
repro_20robot_collision.py — Step 2 Reproducer: 20 Robots + 3 Fixed Stations.

Runs a live multi-process simulation using FleetOrchestrator across:
- 20 real OS child AMR processes (AMR-01 to AMR-20)
- 3 fixed station processes (IMPORT_STATION, EXPORT_STATION, AUTHORITY_STATION)
- Target: 300+ ticks

Tracks and records:
1. True Passthrough: Any robot that occupies the same cell as another robot for > 1 tick.
2. Ping-Pong Oscillation: Any robot oscillating between the same 2 cells for >= 5 consecutive ticks.
3. Pod Slot Violation: Any path/position crossing a currently-occupied pod slot.
"""

import multiprocessing as mp
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.world import build_default_world
from app.services.fleet_orchestrator import FleetOrchestrator


def clean_stale_ports(ports=range(9001, 9030)):
    if sys.platform != "win32":
        return
    import subprocess
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "udp"], text=True)
        my_pid = os.getpid()
        killed = set()
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4 and parts[0].upper() == "UDP":
                addr = parts[1]
                pid_str = parts[-1]
                for p in list(ports) + [9601, 9602, 9603]:
                    if f":{p}" in addr:
                        try:
                            pid = int(pid_str)
                            if pid != my_pid and pid not in killed and pid > 0:
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", str(pid)],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                                killed.add(pid)
                        except Exception:
                            pass
        if killed:
            time.sleep(0.5)
    except Exception:
        pass


def run_20robot_repro(target_ticks: int = 320, tick_interval_s: float = 0.03, num_robots: int = 20) -> Tuple[bool, Dict[str, Any]]:
    clean_stale_ports()

    world = build_default_world()
    pod_slots_set = set(world.pod_slots.values())

    starts = [
        # West staging bays
        (2, 4), (2, 7), (2, 10), (2, 13), (2, 16), (2, 19), (2, 22), (2, 25),
        # East staging bays
        (27, 4), (27, 7), (27, 10), (27, 13), (27, 16), (27, 19), (27, 22), (27, 25),
        # North & South cross-highways
        (10, 3), (19, 3), (10, 26), (19, 26),
        # Extra transit staging bays for up to 25 robots
        (14, 3), (16, 3), (14, 26), (16, 26), (10, 14)
    ]

    robot_types = (
        ["GOODS_TO_PERSON"] * 10 +
        ["SORTING"] * 8 +
        ["SCANNING_AUDIT"] * 7
    )

    goals = [
        # Eastbound robots
        (27, 4), (27, 12), (27, 10), (27, 15),
        (27, 18), (27, 21), (27, 24), (27, 25),
        # Westbound robots (direct head-on & cross paths)
        (2, 4), (2, 10), (2, 7), (2, 16),
        (2, 13), (2, 22), (2, 19), (2, 25),
        # North-South & South-North cross-highway traffic
        (10, 26), (19, 26), (10, 3), (19, 3),
        # Extra goals
        (14, 26), (16, 26), (14, 3), (16, 3), (19, 14)
    ]

    robots_config = [
        {
            "robot_id": f"AMR-{i:02d}",
            "start": starts[i - 1],
            "goal": goals[i - 1],
            "urgency": 1 + (i % 5),
            "battery_pct": 100.0,
            "robot_type": robot_types[i - 1],
            "enable_idle_audit": (robot_types[i - 1] == "SCANNING_AUDIT"),
        }
        for i in range(1, num_robots + 1)
    ]

    log_dir = ROOT_DIR / "logs" / "repro_20robot"
    log_dir.mkdir(parents=True, exist_ok=True)

    orchestrator = FleetOrchestrator(
        robots_config=robots_config,
        tick_interval_s=tick_interval_s,
        max_ticks=target_ticks,
        log_dir=log_dir,
    )

    # Tracking data structures
    tick_robot_positions: Dict[int, Dict[str, Tuple[int, int]]] = defaultdict(dict)
    robot_position_history = defaultdict(list)
    robot_last_recorded_tick = defaultdict(lambda: -1)
    robot_state_history = defaultdict(list)
    pod_occupancy_violations = []

    print("\n" + "=" * 80)
    print(f"STEP 2 REPRO RUN: 20 ROBOTS + 3 STATIONS ACROSS INDEPENDENT OS PROCESSES")
    print(f"Target: {target_ticks} ticks | Interval: {tick_interval_s*1000:.1f}ms")
    print("=" * 80)

    orchestrator.start(enable_stations=True)

    start_time = time.time()
    max_wall_time = 65.0
    last_reported = 0

    try:
        while time.time() - start_time < max_wall_time:
            tick_update = orchestrator.bus.process_incoming()
            curr_tick = orchestrator.bus.current_tick

            if tick_update:
                robots = tick_update.get("robots", [])
                carried_pod_positions = {}

                for r in robots:
                    rid = r["id"]
                    r_tick = int(r.get("tick", curr_tick))
                    r_pos = (int(r["x"]), int(r["y"]))
                    r_state = r.get("state", "IDLE")
                    r_pod = r.get("carrying_pod_id")

                    # Map robot position to its actual tick
                    tick_robot_positions[r_tick][rid] = r_pos

                    if r_tick > robot_last_recorded_tick[rid]:
                        robot_last_recorded_tick[rid] = r_tick
                        robot_position_history[rid].append(r_pos)
                        robot_state_history[rid].append((r_tick, r_state))

                    if (r_pod or r_state in ("PICKING", "LIFTING")) and r_tick == curr_tick:
                        carried_pod_positions[r_pos] = (rid, r_pod)

                # Check invariant: No robot's path intersects a pod cell occupied/carried by another robot at the same tick
                for r in robots:
                    rid = r["id"]
                    r_tick = int(r.get("tick", curr_tick))
                    if r_tick != curr_tick:
                        continue
                    r_path = r.get("path", [])
                    for step in r_path:
                        step_pos = (int(step["x"]), int(step["y"]))
                        step_t = int(step.get("t", curr_tick))
                        if step_t == curr_tick and step_pos in carried_pod_positions:
                            owner_id, pod_id = carried_pod_positions[step_pos]
                            if owner_id != rid:
                                pod_occupancy_violations.append({
                                    "tick": curr_tick,
                                    "intruder": rid,
                                    "owner": owner_id,
                                    "pod_id": pod_id,
                                    "cell": step_pos,
                                })

                if curr_tick >= last_reported + 50:
                    last_reported = curr_tick
                    print(f"  [Progress] Tick {curr_tick}/{target_ticks} ({len(robots)} active robots reporting)...")

            if curr_tick >= target_ticks:
                print(f"  [Reached target] Simulation completed {curr_tick} ticks.")
                break

            time.sleep(0.005)

    finally:
        orchestrator.stop()
        clean_stale_ports()

    final_tick = orchestrator.bus.current_tick
    print(f"\nCompleted run with final tick: {final_tick}")

    # ──────────────────────────────────────────────────────────────────────────
    # METRIC 1: True Passthrough (Robots in SAME cell for > 1 tick)
    # ──────────────────────────────────────────────────────────────────────────
    multi_tick_collisions = []
    # Check consecutive ticks
    all_ticks = sorted(tick_robot_positions.keys())
    for idx in range(len(all_ticks) - 1):
        t1 = all_ticks[idx]
        t2 = all_ticks[idx + 1]
        if t2 != t1 + 1:
            continue
        pos1 = tick_robot_positions[t1]
        pos2 = tick_robot_positions[t2]

        # Find collisions at t1
        cell_to_robots_1 = defaultdict(list)
        for rid, pos in pos1.items():
            cell_to_robots_1[pos].append(rid)

        # Find collisions at t2
        cell_to_robots_2 = defaultdict(list)
        for rid, pos in pos2.items():
            cell_to_robots_2[pos].append(rid)

        for cell, rids_1 in cell_to_robots_1.items():
            if len(rids_1) > 1:
                # Same cell has multiple robots at t1. Did any pair remain in the same cell at t2?
                for r1 in rids_1:
                    for r2 in rids_1:
                        if r1 < r2:
                            if pos2.get(r1) == pos2.get(r2) == cell:
                                multi_tick_collisions.append({
                                    "ticks": (t1, t2),
                                    "robots": (r1, r2),
                                    "cell": cell,
                                })

    # ──────────────────────────────────────────────────────────────────────────
    # METRIC 2: Ping-Pong Oscillations (Same 2 cells for >= 5 consecutive ticks)
    # ──────────────────────────────────────────────────────────────────────────
    ping_pong_oscillations = []
    for rid, positions in robot_position_history.items():
        if len(positions) < 6:
            continue
        cur_osc = 0
        max_osc = 0
        for i in range(2, len(positions)):
            if positions[i] == positions[i - 2] and positions[i] != positions[i - 1]:
                cur_osc += 1
                if cur_osc > max_osc:
                    max_osc = cur_osc
            else:
                cur_osc = 0
        if max_osc >= 5:
            ping_pong_oscillations.append({"robot_id": rid, "osc_ticks": max_osc})

    # Summary
    results = {
        "final_tick": final_tick,
        "multi_tick_collisions": multi_tick_collisions,
        "ping_pong_oscillations": ping_pong_oscillations,
        "pod_occupancy_violations": pod_occupancy_violations,
    }

    print("\n" + "=" * 80)
    print("STEP 2 AUDIT FINDINGS (20 ROBOTS + 3 STATIONS)")
    print("=" * 80)
    print(f"1. True Passthroughs (> 1 tick overlap): {len(multi_tick_collisions)}")
    for c in multi_tick_collisions[:10]:
        print(f"   -> [Ticks {c['ticks'][0]}-{c['ticks'][1]}] {c['robots'][0]} & {c['robots'][1]} at cell {c['cell']}")

    print(f"2. Ping-Pong Oscillations (>= 5 ticks): {len(ping_pong_oscillations)}")
    for o in ping_pong_oscillations:
        print(f"   -> {o['robot_id']} oscillated for {o['osc_ticks']} ticks")

    print(f"3. Pod Slot Path Violations: {len(pod_occupancy_violations)}")
    for v in pod_occupancy_violations[:10]:
        print(f"   -> [Tick {v['tick']}] {v['intruder']} path crossed cell {v['cell']} occupied/carried by {v['owner']} (pod={v['pod_id']})")

    success = (len(multi_tick_collisions) == 0 and
               len(ping_pong_oscillations) == 0 and
               len(pod_occupancy_violations) == 0 and
               final_tick >= 300)

    print(f"\nOVERALL RESULT: {'PASSED (Zero defects)' if success else 'FAILED (Regression reproduced)'}")
    print("=" * 80 + "\n")
    return success, results


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    success, res = run_20robot_repro(num_robots=count)
    sys.exit(0 if success else 1)
