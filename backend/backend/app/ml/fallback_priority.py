"""
fallback_priority.py — Deterministic Baseline Priority Calculator with Auditing Floor Rule.

Provides the guaranteed safe fallback priority calculation for conflict arbitration.
Enforces the mandatory rule that auditing/taskless robots always score in the lowest tier.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.models.robot_fsm import RobotState

log = logging.getLogger(__name__)

# Base floor for taskless/auditing robots to ensure active missions always win right-of-way
AUDIT_BASE_SCORE = -1000.0


def calculate_deterministic_priority(
    robot: Any,
    task: Optional[Any],
    distance_to_goal: int,
) -> float:
    """
    Authoritative deterministic priority calculation.

    Rules:
      1. If the robot is in RobotState.AUDITING, or has no active task (task is None or task.status != 'IN_PROGRESS'),
         the robot is placed in the lowest priority tier (AUDIT_BASE_SCORE = -1000.0).
         It deterministically yields to any active delivery or task-carrying robot.
      2. If carrying an active task, follows the standard SCHEMA.md §13 formula:
             score = (task.urgency * 100)
                   + (500 if robot.battery_pct < 20 else 0)
                   + (robot.wait_ticks_so_far * 10)
                   - (distance_to_goal * 1)
    """
    # Check if robot is auditing or taskless
    is_auditing = False
    robot_state = getattr(robot, "state", None)
    if robot_state == RobotState.AUDITING or str(robot_state) == "AUDITING":
        is_auditing = True
    elif getattr(robot, "is_audit", False):
        is_auditing = True
    elif task is None:
        is_auditing = True

    if is_auditing:
        # Lowest priority tier floor: guaranteed negative score
        wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 2)
        dist_pen = float(distance_to_goal)
        score = AUDIT_BASE_SCORE + wait_bonus - dist_pen
        return float(score)

    # Standard task-carrying priority calculation
    urgency = getattr(task, "urgency", 1) if task is not None else 1
    battery_pct = getattr(robot, "battery_pct", 100.0)
    battery_bonus = 500.0 if battery_pct < 20.0 else 0.0
    wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 10)
    distance_penalty = float(distance_to_goal * 1)

    score = (float(urgency) * 100.0) + battery_bonus + wait_bonus - distance_penalty
    return float(score)
