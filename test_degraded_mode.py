"""
test_degraded_mode.py — Unit tests for DegradedModeDetector.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.services.degraded_mode import DegradedModeDetector


def test_normal_network_mode():
    """When peer updates are fresh, robot operates in normal full-speed mode."""
    detector = DegradedModeDetector(threshold_missing_ticks=3)
    detector.record_peer_tick("AMR-02", 10)

    # Tick 11 -> lag is 1 tick -> normal mode
    assert detector.is_degraded(current_tick=11) is False
    assert detector.should_move_this_tick(current_tick=11) is True


def test_degraded_network_speed_reduction():
    """When peer updates lag by >= 3 ticks, degraded mode cuts speed to 50%."""
    detector = DegradedModeDetector(threshold_missing_ticks=3)
    detector.record_peer_tick("AMR-02", 5)

    # At tick 9, lag is 4 ticks -> degraded!
    assert detector.is_degraded(current_tick=9) is True

    # In degraded mode, only moves on even ticks (50% speed)
    assert detector.should_move_this_tick(current_tick=9) is False  # Odd tick -> pause
    assert detector.should_move_this_tick(current_tick=10) is True  # Even tick -> move
    assert detector.should_move_this_tick(current_tick=11) is False


def test_forced_degraded_mode():
    """Forced degraded mode activates 50% speed limit immediately."""
    detector = DegradedModeDetector()
    detector.set_forced_degraded(True)

    assert detector.is_degraded(current_tick=1) is True
    assert detector.should_move_this_tick(current_tick=1) is False
    assert detector.should_move_this_tick(current_tick=2) is True
