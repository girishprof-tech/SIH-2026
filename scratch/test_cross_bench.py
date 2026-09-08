import os, sys
from pathlib import Path
ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from benchmark_coordination_policies import run_scenario_with_policy

open_lanes = [1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28]

def get_cross_scenario(seed, fleet_size):
    half = fleet_size // 2
    obstacles = []
    for y in (5, 10, 15, 20):
        for x in range(3, 27):
            if x not in (7, 14, 21): obstacles.append((x, y))
    cfgs = []
    for i in range(fleet_size):
        rid = f"AMR-{i+1:02d}"
        if i < half:
            s = (2, open_lanes[i])
            g = (27, open_lanes[half - 1 - i])
        else:
            idx = i - half
            s = (27, open_lanes[idx])
            g = (2, open_lanes[half - 1 - idx])
        cfgs.append({"robot_id": rid, "start": s, "goal": g, "urgency": (i % 5) + 1, "battery_pct": 100.0})
    return {"fleet_size": fleet_size, "seed": seed, "obstacles": obstacles, "robots_config": cfgs}

for fs in [5, 10, 15]:
    for seed in [1000, 1001]:
        sc = get_cross_scenario(seed, fs)
        r_dec = run_scenario_with_policy(sc, "decentralized")
        r_snw = run_scenario_with_policy(sc, "stop_and_wait")
        pct = (r_snw["completion_time"] - r_dec["completion_time"]) / r_snw["completion_time"] * 100.0
        print(f"FS={fs} S={seed}: Dec={r_dec['completion_time']} (col={r_dec['collisions']}) | SnW={r_snw['completion_time']} (col={r_snw['collisions']}) -> Reduction={pct:.1f}%")
