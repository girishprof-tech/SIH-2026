"""
robot_fsm.py — Explicit Deterministic Finite State Machine for Autonomous AMRs.

Unified single source of truth for robot states and transitions, supporting
G2P Pod Transport (LIFTING, LOWERING), Sortation AMRs, Auditing, and Failsafe recovery.
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
    LIFTING = "LIFTING"
    EN_ROUTE_DROPOFF = "EN_ROUTE_DROPOFF"
    DROPPING = "DROPPING"
    LOWERING = "LOWERING"
    CONFLICT_NEGOTIATING = "CONFLICT_NEGOTIATING"
    DOCKING = "DOCKING"
    EGRESS = "EGRESS"
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
    DOCK_COMPLETE = "DOCK_COMPLETE"
    LIFT_COMPLETE = "LIFT_COMPLETE"
    EGRESS_COMPLETE = "EGRESS_COMPLETE"
    DROPOFF_REACHED = "DROPOFF_REACHED"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    LOWER_COMPLETE = "LOWER_COMPLETE"
    AUDIT_CHECKPOINT_LOGGED = "AUDIT_CHECKPOINT_LOGGED"
    RESUME_PICKUP = "RESUME_PICKUP"
    RESUME_DROPOFF = "RESUME_DROPOFF"
    RESUME_AUDIT = "RESUME_AUDIT"
    RESUME_IDLE = "RESUME_IDLE"
    BATTERY_LOW = "BATTERY_LOW"
    CHARGE_COMPLETE = "CHARGE_COMPLETE"
    E_STOP = "E_STOP"
    RESET = "RESET"
    FAILSAFE_RESET = "FAILSAFE_RESET"


# Exact transition dictionary: (Current State, Event) -> Next State
TRANSITIONS: Dict[Tuple[RobotState, RobotEvent], RobotState] = {
    # Standard Mission lifecycle
    (RobotState.IDLE, RobotEvent.TASK_RECEIVED): RobotState.ASSIGNED,
    (RobotState.IDLE, RobotEvent.START_AUDIT): RobotState.AUDITING,
    (RobotState.IDLE, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.ASSIGNED, RobotEvent.PATH_PLANNED): RobotState.EN_ROUTE_PICKUP,
    (RobotState.EN_ROUTE_PICKUP, RobotEvent.PICKUP_REACHED): RobotState.PICKING,
    (RobotState.EN_ROUTE_PICKUP, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.PICKING, RobotEvent.PICKUP_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.PICKING, RobotEvent.LIFT_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.EN_ROUTE_DROPOFF, RobotEvent.DROPOFF_REACHED): RobotState.DROPPING,
    (RobotState.EN_ROUTE_DROPOFF, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.DROPPING, RobotEvent.MISSION_COMPLETE): RobotState.IDLE,
    (RobotState.DROPPING, RobotEvent.LOWER_COMPLETE): RobotState.IDLE,

    # G2P Pod Transport Lifecycle (with DOCKING + EGRESS)
    (RobotState.EN_ROUTE_PICKUP, RobotEvent.DOCK_COMPLETE): RobotState.DOCKING,
    (RobotState.DOCKING, RobotEvent.PICKUP_REACHED): RobotState.PICKING,
    (RobotState.DOCKING, RobotEvent.LIFT_COMPLETE): RobotState.EGRESS,
    (RobotState.PICKING, RobotEvent.DOCK_COMPLETE): RobotState.DOCKING,
    (RobotState.LIFTING, RobotEvent.LIFT_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.LIFTING, RobotEvent.PICKUP_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.LIFTING, RobotEvent.EGRESS_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.EGRESS, RobotEvent.EGRESS_COMPLETE): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.EGRESS, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.LOWERING, RobotEvent.LOWER_COMPLETE): RobotState.IDLE,
    (RobotState.LOWERING, RobotEvent.MISSION_COMPLETE): RobotState.IDLE,

    # Audit lifecycle
    (RobotState.AUDITING, RobotEvent.AUDIT_CHECKPOINT_LOGGED): RobotState.IDLE,
    (RobotState.AUDITING, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,

    # Deterministic Conflict Resume Events
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.CONFLICT_LOST): RobotState.CONFLICT_NEGOTIATING,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_PICKUP): RobotState.EN_ROUTE_PICKUP,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_DROPOFF): RobotState.EN_ROUTE_DROPOFF,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_AUDIT): RobotState.AUDITING,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.RESUME_IDLE): RobotState.IDLE,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.MISSION_COMPLETE): RobotState.IDLE,
    (RobotState.CONFLICT_NEGOTIATING, RobotEvent.EGRESS_COMPLETE): RobotState.EN_ROUTE_DROPOFF,

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


def get_next_state(current_state: RobotState, event: RobotEvent) -> RobotState:
    """Pure function returning next state or FAILSAFE_HOLD."""
    if event in GLOBAL_EVENTS:
        return GLOBAL_EVENTS[event]
    return TRANSITIONS.get((current_state, event), RobotState.FAILSAFE_HOLD)


class RobotFSM:
    """Deterministic State Machine Driver for a Robot."""

    def __init__(self, initial_state: RobotState = RobotState.IDLE) -> None:
        self.state: RobotState = initial_state

    def transition(self, event: RobotEvent) -> RobotState:
        """
        Executes a deterministic state transition.
        If transition is invalid, deterministically falls back to FAILSAFE_HOLD.
        """
        next_state = get_next_state(self.state, event)
        if next_state == RobotState.FAILSAFE_HOLD and (self.state, event) not in TRANSITIONS and event not in GLOBAL_EVENTS:
            log.warning(
                f"Invalid FSM transition attempted: state={self.state.value}, event={event.value}. "
                f"Deterministically transitioning to FAILSAFE_HOLD."
            )
        self.state = next_state
        return self.state

    def can_transition(self, event: RobotEvent) -> bool:
        """Check if an event is legally allowed from the current state."""
        return event in GLOBAL_EVENTS or (self.state, event) in TRANSITIONS
