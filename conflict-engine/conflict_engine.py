"""
conflict_engine.py — Unified integration entry point for the simulation loop.
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
SIH26123 — Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses.

Deliverable 5:
Exposes run_conflict_engine_tick(), the single authoritative call invoked once per simulation tick
by Member 4's backend simulation engine.
Pipeline:
  1. Recalculates priority scores for EN_ROUTE robots.
  2. Detects spatial and temporal conflicts (cell overlaps and head-on swaps).
  3. Resolves each detected conflict via deterministic arbitration and replanning.
  4. Returns a comprehensive telemetry and resolution summary.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

from arbitration import resolve_conflict
from conflict_detector import detect_conflicts
from models import RobotState
from priority import calculate_priority_score


def run_conflict_engine_tick(
    robots: Dict[str, Any],
    tasks: Dict[str, Any],
    reservation_table: Dict[Tuple[int, int, int], str],
    current_tick: int,
    find_path_fn: Callable[..., List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Full arbitration pipeline executed once per simulation tick:
    1. Recalculates priority_score for every EN_ROUTE robot using calculate_priority_score().
    2. Runs detect_conflicts() across all active robots.
    3. For each detected conflict, executes resolve_conflict() to yield the lower-priority AMR.
    4. Returns a summary dictionary conforming to backend broker requirements.

    Args:
        robots: Dict mapping robot_id to Robot instances.
        tasks: Dict mapping task_id to Task instances.
        reservation_table: Master (x, y, t) -> robot_id reservation map.
        current_tick: Clock tick for the current frame.
        find_path_fn: Pathfinding function conforming to Member 2 contract.

    Returns:
        Dict:
        {
            "conflicts_found": int,
            "resolutions": list[dict],
            "updated_robots": dict[str, Robot]
        }
    """
    # 1. Update priority scores for all EN_ROUTE robots
    for robot in robots.values():
        is_en_route = (
            robot.state == RobotState.EN_ROUTE or
            robot.state == "EN_ROUTE" or
            getattr(robot.state, "value", None) == "EN_ROUTE"
        )
        if is_en_route:
            task = tasks.get(robot.current_task_id) if getattr(robot, "current_task_id", None) else None
            dist = robot.distance_to_goal() if hasattr(robot, "distance_to_goal") else 0
            robot.priority_score = calculate_priority_score(robot, task, dist)

    # 2. Detect conflicts across the active fleet
    conflicts = detect_conflicts(list(robots.values()), current_tick)

    # 3. Resolve all identified conflicts sequentially
    resolutions: List[Dict[str, Any]] = []
    for conflict in conflicts:
        conflict["current_tick"] = current_tick
        res = resolve_conflict(conflict, robots, reservation_table, find_path_fn)
        resolutions.append(res)

    return {
        "conflicts_found": len(conflicts),
        "resolutions": resolutions,
        "updated_robots": robots,
    }
