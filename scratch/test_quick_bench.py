import os, sys
from pathlib import Path
ROOT_DIR = Path(".").resolve()
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))
from benchmark_coordination_policies import generate_overlapping_scenario, run_scenario_with_policy

for fs in [5, 10, 15]:
    for seed in [1000, 1001, 1002]:
        sc = generate_overlapping_scenario(seed, fs)
        r_dec = run_scenario_with_policy(sc, "decentralized")
        r_snw = run_scenario_with_policy(sc, "stop_and_wait")
        print(f"FS={fs} Seed={seed}: Dec={r_dec['completion_time']} (col={r_dec['collisions']}, w={r_dec['total_wait_ticks']}) | SnW={r_snw['completion_time']} (col={r_snw['collisions']}, w={r_snw['total_wait_ticks']})")
