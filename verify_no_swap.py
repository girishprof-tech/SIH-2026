"""
Verification script to verify no swap collisions or vertex collisions occur
across all robot processes in real multi-process decentralized execution logs.
"""
import re
import os
import glob
import sys

def parse_robot_logs(log_dir="logs"):
    pattern = os.path.join(log_dir, "robot_*.log")
    files = glob.glob(pattern)
    if not files:
        print(f"Error: No log files found in {log_dir}")
        sys.exit(1)

    # regex to match: [Tick X] Pos=(x, y), ...
    # e.g., [17:24:09] [Tick 8] Pos=(9, 6), Heading=EAST, ...
    tick_regex = re.compile(r"\[Tick\s+(\d+)\]\s+Pos=\((\d+),\s*(\d+)\)")
    
    # robot_trajectories: robot_id -> {tick -> (x, y)}
    robot_trajectories = {}
    
    for fpath in sorted(files):
        fname = os.path.basename(fpath)
        robot_id = fname.replace("robot_", "").replace(".log", "")
        trajectory = {}
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                m = tick_regex.search(line)
                if m:
                    tick = int(m.group(1))
                    pos = (int(m.group(2)), int(m.group(3)))
                    # Keep the last reported pos for that tick
                    trajectory[tick] = pos
        robot_trajectories[robot_id] = trajectory
        print(f"Parsed {robot_id}: {len(trajectory)} ticks logged (ticks {min(trajectory.keys(), default=0)} to {max(trajectory.keys(), default=0)})")

    return robot_trajectories

def verify_safety(robot_trajectories):
    robot_ids = sorted(robot_trajectories.keys())
    all_ticks = set()
    for traj in robot_trajectories.values():
        all_ticks.update(traj.keys())
    
    sorted_ticks = sorted(all_ticks)
    swap_violations = []
    cell_violations = []

    for t in sorted_ticks:
        # Check 1: Cell collisions at tick t
        positions_at_t = {}
        for r_id in robot_ids:
            if t in robot_trajectories[r_id]:
                pos = robot_trajectories[r_id][t]
                if pos in positions_at_t:
                    cell_violations.append((t, positions_at_t[pos], r_id, pos))
                else:
                    positions_at_t[pos] = r_id

        # Check 2: Swap collisions between tick t-1 and tick t
        if t - 1 in all_ticks:
            for i in range(len(robot_ids)):
                for j in range(i + 1, len(robot_ids)):
                    r1 = robot_ids[i]
                    r2 = robot_ids[j]
                    traj1 = robot_trajectories[r1]
                    traj2 = robot_trajectories[r2]
                    
                    if (t - 1 in traj1) and (t in traj1) and (t - 1 in traj2) and (t in traj2):
                        prev1, now1 = traj1[t - 1], traj1[t]
                        prev2, now2 = traj2[t - 1], traj2[t]
                        
                        # Swap check: r1 moves to prev2 AND r2 moves to prev1, while prev1 != prev2
                        if prev1 == now2 and prev2 == now1 and prev1 != prev2:
                            swap_violations.append((t, r1, r2, prev1, prev2))

    print("\n" + "=" * 60)
    print("VERIFICATION RESULTS")
    print("=" * 60)
    print(f"Total ticks checked: {len(sorted_ticks)}")
    print(f"Total robots checked: {len(robot_ids)}")
    print(f"Swap collisions found: {len(swap_violations)}")
    print(f"Cell collisions found: {len(cell_violations)}")
    
    if swap_violations:
        print("\nFAIL: Swap violations detected:")
        for t, r1, r2, p1, p2 in swap_violations:
            print(f"  [Tick {t}] {r1} and {r2} swapped between {p1} and {p2}!")
    else:
        print("  -> PASSED: Zero swap collisions detected across all ticks!")

    if cell_violations:
        print("\nFAIL: Cell collisions detected:")
        for t, r1, r2, p in cell_violations:
            print(f"  [Tick {t}] {r1} and {r2} both occupied cell {p}!")
    else:
        print("  -> PASSED: Zero vertex/cell collisions detected across all ticks!")

    print("=" * 60)
    if not swap_violations and not cell_violations:
        print("OVERALL VERIFICATION: SUCCESS (100% Collision-Free)")
        return True
    else:
        print("OVERALL VERIFICATION: FAILED")
        return False

if __name__ == "__main__":
    trajs = parse_robot_logs()
    success = verify_safety(trajs)
    sys.exit(0 if success else 1)
