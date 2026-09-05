"""
arbitration.py — Priority-based conflict arbitration and yield resolution.
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
SIH26123 — Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses.

Deliverable 4:
Implements resolve_conflict() to arbitrate between conflicting AMRs:
  1. Compares priority scores; higher score wins and maintains route.
  2. In case of an exact tie, the lexicographically lower robot_id wins (e.g. "AMR-01" beats "AMR-03").
  3. The yielding robot enters CONFLICT_NEGOTIATING, wait_ticks_so_far increments,
     its old reservations are purged from reservation_table, and find_path_fn is invoked
     to compute an alternate collision-free trajectory before returning to EN_ROUTE.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, Optional, Tuple

from models import RobotState


def _extract_pos(robot: Any) -> Tuple[int, int]:
    """Extract (x, y) coordinates from robot."""
    if hasattr(robot, "position") and robot.position is not None:
        return (int(robot.position[0]), int(robot.position[1]))
    return (int(robot.x), int(robot.y))


def _extract_goal(robot: Any) -> Tuple[int, int]:
    """Extract the ultimate goal cell from robot's planned path or current position."""
    if getattr(robot, "path", None):
        last = robot.path[-1]
        gx = last["x"] if isinstance(last, dict) or hasattr(last, "__getitem__") else getattr(last, "x")
        gy = last["y"] if isinstance(last, dict) or hasattr(last, "__getitem__") else getattr(last, "y")
        return (int(gx), int(gy))
    return _extract_pos(robot)


def resolve_conflict(
    conflict: Dict[str, Any],
    robots: Dict[str, Any],
    reservation_table: Dict[Tuple[int, int, int], str],
    find_path_fn: Callable[..., List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Given a detected conflict, decides which robot yields and which proceeds.

    Rules:
    1. Compare robots[robot_ids[0]].priority_score vs robots[robot_ids[1]].priority_score.
    2. Higher score wins and keeps its current path unchanged.
    3. Lower score robot:
       - State set to CONFLICT_NEGOTIATING.
       - wait_ticks_so_far incremented by 1 (starvation prevention).
       - OLD reservations removed from reservation_table.
       - Computes a NEW path by calling find_path_fn() with updated reservation_table
         (which still preserves the winner's claims).
       - State set back to EN_ROUTE once new path is assigned.
    4. Exact tie: Lexicographically lower robot_id wins (e.g., "AMR-01" beats "AMR-03").
    5. Never deletes or overwrites another robot's reservations.

    Args:
        conflict: Conflict record containing "robot_ids", "cell", "tick", "type".
        robots: Mapping of robot_id -> Robot instance.
        reservation_table: Shared space-time reservation lookup (x, y, t) -> robot_id.
        find_path_fn: Injected pathfinder function.

    Returns:
        Summary dict containing:
        {
            "winner_id": str,
            "loser_id": str,
            "loser_new_path": list[dict],
            "resolution_type": "YIELD_AND_REPLAN",
            "conflict_type": str,
            "cell": dict
        }
    """
    id_a, id_b = conflict["robot_ids"][0], conflict["robot_ids"][1]
    robot_a = robots[id_a]
    robot_b = robots[id_b]

    score_a = float(robot_a.priority_score)
    score_b = float(robot_b.priority_score)

    # 1 & 4. Determine winner and loser
    if score_a > score_b:
        winner, loser = robot_a, robot_b
    elif score_b > score_a:
        winner, loser = robot_b, robot_a
    else:
        # Tie-breaker: lexicographically lower robot_id wins
        if robot_a.robot_id < robot_b.robot_id:
            winner, loser = robot_a, robot_b
        else:
            winner, loser = robot_b, robot_a

    # 3. Transition loser to CONFLICT_NEGOTIATING and increment wait count
    # Support both enum and string for state compatibility
    loser.state = RobotState.CONFLICT_NEGOTIATING
    if hasattr(loser, "wait_ticks_so_far"):
        loser.wait_ticks_so_far += 1
    elif hasattr(loser, "_wait_ticks"):
        loser._wait_ticks += 1

    # Purge ONLY the loser's reservations from the reservation table (Rule 5)
    stale_keys = [k for k, owner in reservation_table.items() if owner == loser.robot_id]
    for k in stale_keys:
        del reservation_table[k]

    # Compute new path for loser
    start_pos = _extract_pos(loser)
    goal_pos = _extract_goal(loser)
    current_tick = conflict.get("current_tick", getattr(loser, "last_updated_tick", 0))
    if current_tick <= 0:
        current_tick = max(0, conflict.get("tick", 1) - 1)

    # Call find_path_fn adaptively (handles 4-arg mocks or full Member 2 signatures)
    try:
        sig = inspect.signature(find_path_fn)
        if "robot_id" in sig.parameters:
            new_path = find_path_fn(
                start_pos, goal_pos, current_tick, reservation_table, robot_id=loser.robot_id
            )
        else:
            new_path = find_path_fn(start_pos, goal_pos, current_tick, reservation_table)
    except (TypeError, ValueError):
        new_path = find_path_fn(start_pos, goal_pos, current_tick, reservation_table)

    if new_path is None:
        new_path = []

    # Assign new path to loser
    # If loser uses PathNode objects (Member 4 backend), convert if necessary
    if getattr(loser, "path", None) and hasattr(loser.path[0], "t") and not isinstance(loser.path[0], dict):
        try:
            from app.models.robot import PathNode
            loser.path = [PathNode(x=p["x"], y=p["y"], t=p["t"]) for p in new_path]
        except (ImportError, KeyError, TypeError):
            loser.path = new_path
    else:
        loser.path = new_path

    if hasattr(loser, "_path_idx"):
        loser._path_idx = 0
    if hasattr(loser, "_needs_replan"):
        loser._needs_replan = False

    # Loser returns to EN_ROUTE with new path assigned
    loser.state = RobotState.EN_ROUTE

    return {
        "winner_id": winner.robot_id,
        "loser_id": loser.robot_id,
        "loser_new_path": new_path,
        "resolution_type": "YIELD_AND_REPLAN",
        "conflict_type": conflict.get("type", "UNKNOWN"),
        "cell": conflict.get("cell", {"x": start_pos[0], "y": start_pos[1]}),
    }
