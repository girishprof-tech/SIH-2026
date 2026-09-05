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

    Args:
        robot: Robot instance containing battery_pct and wait_ticks_so_far.
        task: Optional Task instance containing urgency (defaults to 1 if None).
        distance_to_goal: Manhattan distance to the destination cell.

    Returns:
        float: Priority score where higher values represent greater arbitration priority.
    """
    urgency = task.urgency if task is not None and hasattr(task, "urgency") else 1
    battery_bonus = 500.0 if robot.battery_pct < 20.0 else 0.0
    wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 10)
    distance_penalty = float(distance_to_goal * 1)

    score = (float(urgency) * 100.0) + battery_bonus + wait_bonus - distance_penalty
    return float(score)
