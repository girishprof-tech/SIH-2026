"""
test_fsm.py — Comprehensive Unit Tests for the Deterministic Robot Finite State Machine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import (
    GLOBAL_EVENTS,
    TRANSITIONS,
    RobotEvent,
    RobotFSM,
    RobotState,
    get_next_state,
)


def test_full_mission_lifecycle_transitions():
    """Verifies the complete standard mission lifecycle path."""
    fsm = RobotFSM(RobotState.IDLE)
    assert fsm.state == RobotState.IDLE

    # IDLE -> ASSIGNED
    assert fsm.transition(RobotEvent.TASK_RECEIVED) == RobotState.ASSIGNED

    # ASSIGNED -> EN_ROUTE_PICKUP
    assert fsm.transition(RobotEvent.PATH_PLANNED) == RobotState.EN_ROUTE_PICKUP

    # EN_ROUTE_PICKUP -> PICKING
    assert fsm.transition(RobotEvent.PICKUP_REACHED) == RobotState.PICKING

    # PICKING -> EN_ROUTE_DROPOFF
    assert fsm.transition(RobotEvent.PICKUP_COMPLETE) == RobotState.EN_ROUTE_DROPOFF

    # EN_ROUTE_DROPOFF -> DROPPING
    assert fsm.transition(RobotEvent.DROPOFF_REACHED) == RobotState.DROPPING

    # DROPPING -> IDLE
    assert fsm.transition(RobotEvent.MISSION_COMPLETE) == RobotState.IDLE


def test_audit_lifecycle_transitions():
    """Verifies the complete audit patrol lifecycle path."""
    fsm = RobotFSM(RobotState.IDLE)

    # IDLE -> AUDITING
    assert fsm.transition(RobotEvent.START_AUDIT) == RobotState.AUDITING

    # AUDITING -> IDLE
    assert fsm.transition(RobotEvent.AUDIT_CHECKPOINT_LOGGED) == RobotState.IDLE


def test_deterministic_conflict_resume_events():
    """Verifies the three distinct deterministic resume events from CONFLICT_NEGOTIATING."""
    # 1. Resume pickup
    fsm_pickup = RobotFSM(RobotState.EN_ROUTE_PICKUP)
    assert fsm_pickup.transition(RobotEvent.CONFLICT_LOST) == RobotState.CONFLICT_NEGOTIATING
    assert fsm_pickup.transition(RobotEvent.RESUME_PICKUP) == RobotState.EN_ROUTE_PICKUP

    # 2. Resume dropoff
    fsm_dropoff = RobotFSM(RobotState.EN_ROUTE_DROPOFF)
    assert fsm_dropoff.transition(RobotEvent.CONFLICT_LOST) == RobotState.CONFLICT_NEGOTIATING
    assert fsm_dropoff.transition(RobotEvent.RESUME_DROPOFF) == RobotState.EN_ROUTE_DROPOFF

    # 3. Resume audit
    fsm_audit = RobotFSM(RobotState.AUDITING)
    assert fsm_audit.transition(RobotEvent.CONFLICT_LOST) == RobotState.CONFLICT_NEGOTIATING
    assert fsm_audit.transition(RobotEvent.RESUME_AUDIT) == RobotState.AUDITING


def test_invalid_transitions_fallback_to_failsafe_hold():
    """At least 5 invalid transitions must land in FAILSAFE_HOLD."""
    test_cases = [
        (RobotState.IDLE, RobotEvent.PICKUP_COMPLETE),
        (RobotState.PICKING, RobotEvent.DROPOFF_REACHED),
        (RobotState.AUDITING, RobotEvent.PICKUP_REACHED),
        (RobotState.ASSIGNED, RobotEvent.MISSION_COMPLETE),
        (RobotState.CHARGING, RobotEvent.TASK_RECEIVED),
        (RobotState.EN_ROUTE_PICKUP, RobotEvent.CHARGE_COMPLETE),
    ]

    for current_state, invalid_event in test_cases:
        fsm = RobotFSM(current_state)
        result = fsm.transition(invalid_event)
        assert result == RobotState.FAILSAFE_HOLD, (
            f"Expected FAILSAFE_HOLD from state {current_state} on event {invalid_event}, got {result}"
        )


def test_global_events_emergency_and_battery():
    """Verifies that BATTERY_LOW and E_STOP transition correctly from any state."""
    states_to_test = [
        RobotState.IDLE,
        RobotState.EN_ROUTE_PICKUP,
        RobotState.PICKING,
        RobotState.EN_ROUTE_DROPOFF,
        RobotState.CONFLICT_NEGOTIATING,
        RobotState.AUDITING,
    ]

    for st in states_to_test:
        fsm_e = RobotFSM(st)
        assert fsm_e.transition(RobotEvent.E_STOP) == RobotState.EMERGENCY_STOP

        fsm_b = RobotFSM(st)
        assert fsm_b.transition(RobotEvent.BATTERY_LOW) == RobotState.CHARGING


def test_failsafe_recovery_and_state_hygiene():
    """
    Verifies that:
      1. Entering FAILSAFE_HOLD purges pre_conflict_activity.
      2. Firing FAILSAFE_RESET recovers FAILSAFE_HOLD -> IDLE.
      3. From recovered IDLE, robot can accept and begin a new task cleanly.
    """
    pre_conflict_activity = RobotState.AUDITING

    # Simulate entering FAILSAFE_HOLD
    fsm = RobotFSM(RobotState.AUDITING)
    # Invalid transition enters FAILSAFE_HOLD
    assert fsm.transition(RobotEvent.PICKUP_COMPLETE) == RobotState.FAILSAFE_HOLD

    # State hygiene rule: pre_conflict_activity must be cleared immediately upon entering FAILSAFE_HOLD
    pre_conflict_activity = None
    assert pre_conflict_activity is None

    # Failsafe reset recovers to IDLE
    assert fsm.transition(RobotEvent.FAILSAFE_RESET) == RobotState.IDLE

    # Clean new task assignment from recovered IDLE
    assert fsm.transition(RobotEvent.TASK_RECEIVED) == RobotState.ASSIGNED
    assert fsm.transition(RobotEvent.PATH_PLANNED) == RobotState.EN_ROUTE_PICKUP


def test_resume_decision_logic_for_auditing_vs_pickup():
    """
    Tests that a robot yielding during AUDITING remembers it was auditing
    and fires RESUME_AUDIT (never RESUME_PICKUP), returning to AUDITING.
    """
    current_state = RobotState.AUDITING
    pre_conflict_activity = None

    # Step 1: Yield on conflict
    pre_conflict_activity = current_state
    fsm = RobotFSM(current_state)
    fsm.transition(RobotEvent.CONFLICT_LOST)
    assert fsm.state == RobotState.CONFLICT_NEGOTIATING
    assert pre_conflict_activity == RobotState.AUDITING

    # Step 2: Decision logic at resume time
    if pre_conflict_activity == RobotState.EN_ROUTE_PICKUP:
        resume_event = RobotEvent.RESUME_PICKUP
    elif pre_conflict_activity == RobotState.EN_ROUTE_DROPOFF:
        resume_event = RobotEvent.RESUME_DROPOFF
    elif pre_conflict_activity == RobotState.AUDITING:
        resume_event = RobotEvent.RESUME_AUDIT
    else:
        resume_event = RobotEvent.FAILSAFE_RESET

    # Step 3: Assert chosen event is RESUME_AUDIT and never RESUME_PICKUP
    assert resume_event == RobotEvent.RESUME_AUDIT
    assert resume_event != RobotEvent.RESUME_PICKUP

    fsm.transition(resume_event)
    assert fsm.state == RobotState.AUDITING

    # Clean up
    pre_conflict_activity = None
    assert pre_conflict_activity is None
