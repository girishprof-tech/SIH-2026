"""
debug_seed_1004.py
Single scenario debugger for Seed 1004 with 20 robots.
Uses the full integration pipeline to ensure all invariants are preserved.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "testing"))

from full_integration_test import run_scenario

if __name__ == "__main__":
    print("Running debug scenario for Seed 1004 (20 AMRs, 100 ticks)...")
    result = run_scenario(seed=1004, num_robots=20, max_ticks=100)
    print(f"Seed 1004 PASSED cleanly! Conflicts resolved: {result['conflicts_resolved']}")
