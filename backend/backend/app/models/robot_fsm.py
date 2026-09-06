"""
robot_fsm.py — Explicit Deterministic Finite State Machine for Autonomous AMRs.

Unified single source of truth for robot states and transitions.
Any undefined transition deterministically maps to RobotState.FAILSAFE_HOLD.
"""

from __future__ import annotations

import enum
import logging
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)


class RobotState(str, enum.Enum):
    """Authoritative Robot States."""
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    EN_ROUTE_PICKUP = "EN_ROUTE_PICKUP"
    PICKING = "PICKING"
    EN_ROUTE_DROPOFF = "EN_ROUTE_DROPOFF"
    DROPPING = "DROPPING"
    CONFLICT_NEGOTIATING = "CONFLICT_NEGOTIATING"
    AUDITING = "AUDITING"
    CHARGING = "CHARGING"
    FAILSAFE_HOLD = "FAILSAFE_HOLD"
    EMERGENCY_STOP = "EMERGENCY_STOP"

    # Backward-compatibility alias for legacy code
    EN_ROUTE = "EN_ROUTE_PICKUP"


class RobotEvent(str, enum.Enum):
    """Authoritative Robot FSM Events."""
    TASK_RECEIVED = "TASK_RECEIVED"
    START_AUDIT = "START_AUDIT"
    PATH_PLANNED = "PATH_PLANNED"
    PICKUP_REACHED = "PICKUP_REACHED"
    CONFLICT_LOST = "CONFLICT_LOST"
    PICKUP_COMPLETE = "PICKUP_COMPLETE"
    DROPOFF_REACHED = "DROPOFF_REACHED"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    AUDIT_CHECKPOINT_LOGGED = "AUDIT_CHECKPOINT_LOGGED"
    RESUME_PICKUP = "RESUME_PICKUP"
    RESUME_DROPOFF = "RESUME_DROPOFF"
    RESUME_AUDIT = "RESUME_AUDIT"
    BATTERY_LOW = "BATTERY_LOW"
    CHARGE_COMPLETE = "CHARGE_COMPLETE"
    E_STOP = "E_STOP"
    RESET = "RESET"
    FAILSAFE_RESET = "FAILSAFE_RESET"


# Exact transition dictionary: (Current State, Event) -> Next State
TRANSITIONS: Dict[Tuple[RobotState, RobotEvent], RobotState] = {
    # Mission lifecycle
    (RobotState.IDLE, RobotEvent.TASK_RECEIVED): RobotState.ASSIGNED,
    (RobotState.IDLE, RobotEvent.START_AUDIT): RobotState.AUDITING,
    (RobotState.ASSIGNED, RobotEvent.PATH_PLANNED): RobotState.EN_ROUTE_PICKUP,
    (RobotState.EN_ROUTE_PICKUP, RobotEvent.PICKUP_REACHED): RobotState.PICKING,
    (RobotState.EN_ROUTE_PICKUP, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.PICKING, RobotEvent.PICKUP_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.EN_ROUTE_DROPOFF, RobotEvent.DROPOFF_REACHED): RobotState.DROPPING,
    (RobotState.EN_ROUTE_DROPOFF, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.DROPPING, RobotEvent.MISSION_COMPLETE): RobotState.IDLE,

    # Audit lifecycle
    (RobotState.AUDITING, RobotEvent.AUDIT_CHECKPOINT_LOGGED): RobotState.IDLE,
    (RobotState.AUDITING, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,

    # Deterministic Conflict Resume Events
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_PICKUP): RobotState.EN_ROUTE_PICKUP,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_DROPOFF): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_AUDIT): RobotState.AUDITING,

    # Charging lifecycle
    (RobotState.CHARGING, RobotEvent.CHARGE_COMPLETE): RobotState.IDLE,

    # Emergency & Failsafe Recovery
    (RobotState.EMERGENCY_STOP, RobotEvent.RESET): RobotState.IDLE,
    (RobotState.FAILSAFE_HOLD, RobotEvent.FAILSAFE_RESET): RobotState.IDLE,
}

# Global events allowed from any state
GLOBAL_EVENTS: Dict[RobotEvent, RobotState] = {
    RobotEvent.BATTERY_LOW: RobotState.CHARGING,
    RobotEvent.E_STOP: RobotState.EMERGENCY_STOP,
}


class RobotFSM:
    """Deterministic State Machine Driver for a Robot."""

    def __init__(self, initial_state: RobotState = RobotState.IDLE) -> None:
        self.state: RobotState = initial_state

    def transition(self, event: RobotEvent) -> RobotState:
        """
        Transitions to the next state based on the event.
        If the transition is valid, updates self.state and returns it.
        If undefined/invalid, falls back to RobotState.FAILSAFE_HOLD.
        """
        # 1. Global priority events
        if event in GLOBAL_EVENTS:
            next_state = GLOBAL_EVENTS[event]
            self.state = next_state
            return self.state

        # 2. Table-driven transition
        key = (self.state, event)
        if key in TRANSITIONS:
            next_state = TRANSITIONS[key]
            self.state = next_state
            return self.state

        # 3. Invalid transition fallback to FAILSAFE_HOLD
        log.warning(
            "Invalid FSM transition: state=%s event=%s -> entering FAILSAFE_HOLD",
            self.state.value,
            event.value,
        )
        self.state = RobotState.FAILSAFE_HOLD
        return self.state


def get_next_state(current_state: RobotState, event: RobotEvent) -> RobotState:
    """Pure transition function."""
    if event in GLOBAL_EVENTS:
        return GLOBAL_EVENTS[event]
    return TRANSITIONS.get((current_state, event), RobotState.FAILSAFE_HOLD)
