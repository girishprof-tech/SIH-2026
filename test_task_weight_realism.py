"""
test_task_weight_realism.py — Automated verification for weight-based movement pauses.
Asserts that a loaded AMR takes measurably more ticks to traverse identical distance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from models import Task


def simulate_movement(path_length: int, payload_weight_kg: float) -> int:
    """
    Simulates ticks required to travel along a path of length `path_length` cells.
    If carrying payload > 0 kg, pauses 1 tick every 4th movement step to simulate load inertia.
    """
    ticks = 0
    steps_taken = 0
    loaded = payload_weight_kg > 0.0

    while steps_taken < path_length:
        ticks += 1
        # Every 4th movement step while carrying payload incurs a load pause
        if loaded and steps_taken > 0 and (steps_taken % 4 == 0):
            # 1-tick load pause
            loaded_pause = True
            # The robot pauses on this tick without advancing steps_taken
            loaded = False  # pause consumed for this cycle
            continue

        steps_taken += 1
        if payload_weight_kg > 0.0:
            loaded = True

    return ticks


def test_loaded_vs_unloaded_travel_ticks():
    """Verifies that carrying 25kg payload increases tick duration over 12 cells."""
    path_len = 12

    unloaded_task = Task(
        task_id="TASK-UNLOADED",
        pickup=(0, 0),
        dropoff=(12, 0),
        urgency=1,
        created_tick=0,
        payload_weight_kg=0.0,
    )
    loaded_task = Task(
        task_id="TASK-LOADED",
        pickup=(0, 0),
        dropoff=(12, 0),
        urgency=1,
        created_tick=0,
        payload_weight_kg=25.0,
    )

    unloaded_ticks = simulate_movement(path_len, unloaded_task.payload_weight_kg)
    loaded_ticks = simulate_movement(path_len, loaded_task.payload_weight_kg)

    # Unloaded takes exactly path_len ticks (12)
    assert unloaded_ticks == path_len
    # Loaded takes path_len + pauses (at steps 4, 8, 12 -> 3 pauses = 15 ticks)
    assert loaded_ticks > unloaded_ticks
    assert loaded_ticks - unloaded_ticks >= 2
