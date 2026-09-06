"""
test_audit_mission.py — Automated verification for simulated inventory checkpoint patrol.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotEvent, RobotFSM, RobotState
from app.services.audit_mission import AuditMission, DEFAULT_CHECKPOINTS


def test_audit_mission_scan():
    """Verifies that an audit mission records simulated scan count and completes."""
    checkpoint = DEFAULT_CHECKPOINTS[0]
    mission = AuditMission(checkpoint=checkpoint)

    assert mission.is_completed is False
    assert mission.logged_items_count == 0

    log_msg = mission.record_scan(checkpoint)
    assert mission.is_completed is True
    assert mission.logged_items_count > 0
    assert "[AUDIT SIMULATED]" in log_msg
    assert str(checkpoint) in log_msg


def test_idle_robot_audit_loop():
    """
    Simulates an IDLE robot dispatched on an audit patrol for 40 ticks,
    verifying it enters AUDITING, completes checkpoint scan, and returns to IDLE.
    """
    fsm = RobotFSM(RobotState.IDLE)
    checkpoint = (6, 6)
    mission = AuditMission(checkpoint=checkpoint)
    audit_logged = False

    # Simulate tick progression
    for tick in range(40):
        if fsm.state == RobotState.IDLE and not mission.is_completed:
            # Dispatch audit
            fsm.transition(RobotEvent.START_AUDIT)
            assert fsm.state == RobotState.AUDITING

        elif fsm.state == RobotState.AUDITING:
            # Simulate robot traveling to checkpoint and arriving at tick 10
            if tick >= 10 and not mission.is_completed:
                msg = mission.record_scan(checkpoint)
                audit_logged = True
                fsm.transition(RobotEvent.AUDIT_CHECKPOINT_LOGGED)
                assert fsm.state == RobotState.IDLE

    assert audit_logged is True
    assert mission.is_completed is True
    assert fsm.state == RobotState.IDLE
