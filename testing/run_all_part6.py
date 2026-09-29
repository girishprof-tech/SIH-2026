"""
testing/run_all_part6.py
========================
Master test runner for SIH26123 Part 6 Cumulative Attack Suites & Invariants.
Executes in order:
- testing/test_no_dual_runtime.py (Legacy central engine isolation guard)
- testing/attack_step1_idle_fleet.py (Zero autonomous motion until tasked)
- testing/attack_step2_motion.py (Tick-timestamped interpolation & model grounding)
- testing/attack_step3_camera.py (Free camera orbit, boundary clamping & presets)
- testing/attack_step4_collision_shelves.py (Multi-agent collision, edge swap & shelf pass-through)
- testing/attack_step5_map_editor.py (Map format, fuzzing, isolation & launch endpoints)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

TEST_SUITES = [
    ("test_no_dual_runtime", [sys.executable, "testing/test_no_dual_runtime.py"]),
    ("attack_step1_idle_fleet", [sys.executable, "testing/attack_step1_idle_fleet.py"]),
    ("attack_step2_motion", [sys.executable, "testing/attack_step2_motion.py"]),
    ("attack_step3_camera", [sys.executable, "testing/attack_step3_camera.py"]),
    ("attack_step4_collision_shelves", [sys.executable, "testing/attack_step4_collision_shelves.py"]),
    ("attack_step5_map_editor", [sys.executable, "testing/attack_step5_map_editor.py"]),
    ("attack_step6_decentralized_tasks", [sys.executable, "testing/attack_step6_decentralized_tasks.py"]),
    ("verify_step7_e2e_scenarios", [sys.executable, "testing/verify_step7_e2e_scenarios.py"]),
]


def run_all():
    print("=" * 80)
    print("SIH26123 PART 6 — CUMULATIVE ATTACK & REGRESSION TEST RUNNER")
    print("=" * 80)

    total_start = time.time()
    results = []

    for name, cmd in TEST_SUITES:
        print(f"\n>>> Running: {name} ...", flush=True)
        t0 = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        output_lines = []
        try:
            for line in proc.stdout:
                output_lines.append(line)
                clean = line.strip()
                if any(k in clean for k in ["PASSED", "Attack", "ATTACK", "PASS", "COMPLETED", "ALL TESTS"]):
                    print(f"  {clean}", flush=True)
            proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            proc.kill()
            print(f"[TIMEOUT] {name} exceeded 180s")
            sys.exit(1)

        elapsed = time.time() - t0
        passed = (proc.returncode == 0)

        # Print output snippet
        clean_lines = [l.strip() for l in output_lines if l.strip()]
        tail = "\n".join(clean_lines[-6:]) if len(clean_lines) > 6 else "\n".join(clean_lines)
        print(tail)

        status_str = "PASS" if passed else "FAIL"
        print(f"[{status_str}] {name} in {elapsed:.2f}s")
        results.append((name, passed, elapsed, "".join(output_lines)))

        if not passed:
            print(f"\n[ABORT] Suite {name} failed with return code {proc.returncode}")
            sys.exit(1)

    total_elapsed = time.time() - total_start
    print("\n" + "=" * 80)
    print(f"CUMULATIVE TEST SUMMARY: ALL {len(results)}/{len(results)} SUITES PASSED ({total_elapsed:.2f}s)")
    print("=" * 80)
    for name, passed, el, _ in results:
        status_tag = "[PASS]" if passed else "[FAIL]"
        print(f"  {status_tag} {name:<35} {el:>6.2f}s")
    print("=" * 80)


if __name__ == "__main__":
    run_all()
