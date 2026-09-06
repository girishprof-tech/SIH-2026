"""
priority.py — Deterministic priority score calculator.
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
SIH26123 — Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses.

Deliverable 2:
Implements calculate_priority_score per SCHEMA.md §13 formula:
    score = (task.urgency * 100)
          + (500 if robot.battery_pct < 20 else 0)
          + (robot.wait_ticks_so_far * 10)
          - (distance_to_goal * 1)
"""

from __future__ import annotations

from typing import Any, Optional


def calculate_priority_score(robot: Any, task: Optional[Any], distance_to_goal: int) -> float:
    """
    Calculates the authoritative priority score for a robot.

    Formula:
        score = (task.urgency * 100)
              + (500 if robot.battery_pct < 20 else 0)
              + (robot.wait_ticks_so_far * 10)
              - (distance_to_goal * 1)

    Special Rule:
        If robot is in AUDITING state, or task is None (taskless patrol/audit),
        robot is assigned to the lowest priority tier (base -1000.0), ensuring
        it deterministically yields to any active delivery or task-carrying robot.

    Args:
        robot: Robot instance containing battery_pct and wait_ticks_so_far.
        task: Optional Task instance containing urgency.
        distance_to_goal: Manhattan distance to the destination cell.

    Returns:
        float: Priority score where higher values represent greater arbitration priority.
    """
    is_audit = False
    robot_state = getattr(robot, "state", None)
    if robot_state == "AUDITING" or getattr(robot_state, "value", "") == "AUDITING":
        is_audit = True
    elif getattr(robot, "is_audit", False):
        is_audit = True
    elif task is None:
        is_audit = True

    if is_audit:
        wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 2)
        dist_pen = float(distance_to_goal * 1)
        return -1000.0 + wait_bonus - dist_pen

    urgency = task.urgency if task is not None and hasattr(task, "urgency") else 1
    battery_bonus = 500.0 if robot.battery_pct < 20.0 else 0.0
    wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 10)
    distance_penalty = float(distance_to_goal * 1)

    score = (float(urgency) * 100.0) + battery_bonus + wait_bonus - distance_penalty
    return float(score)
