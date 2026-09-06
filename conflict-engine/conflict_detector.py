"""
conflict_detector.py — Real-time spatial and temporal conflict detection.
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
SIH26123 — Edge-AI Based Distributed Fleet Coordination for AMRs in Smart Warehouses.

Deliverable 3:
Implements detect_conflicts() to identify cell overlaps and head-on swap conflicts
within a 2-cell Manhattan-distance neighborhood.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _get_robot_pos(robot: Any) -> Tuple[int, int]:
    """Extract (x, y) from Robot whether represented as tuple position or x, y fields."""
    if hasattr(robot, "position") and robot.position is not None:
        return (int(robot.position[0]), int(robot.position[1]))
    return (int(robot.x), int(robot.y))


def _get_pos_at_tick(robot: Any, tick: int, current_tick: int) -> Tuple[int, int]:
    """
    Returns the predicted (x, y) position of a robot at a specific tick.
    Inspects robot.path (supporting dicts or PathNode instances).
    If tick is beyond the planned path horizon, assumes the robot stays at the final waypoint.
    """
    cur_pos = _get_robot_pos(robot)
    if tick <= current_tick or not getattr(robot, "path", None):
        return cur_pos

    path = robot.path
    # Search path for matching tick
    for node in path:
        nt = node["t"] if isinstance(node, dict) or hasattr(node, "__getitem__") else getattr(node, "t", None)
        if nt == tick:
            nx = node["x"] if isinstance(node, dict) or hasattr(node, "__getitem__") else getattr(node, "x")
            ny = node["y"] if isinstance(node, dict) or hasattr(node, "__getitem__") else getattr(node, "y")
            return (int(nx), int(ny))

    # If beyond the last path node, hold final destination
    last = path[-1]
    last_t = last["t"] if isinstance(last, dict) or hasattr(last, "__getitem__") else getattr(last, "t", None)
    if last_t is not None and tick >= last_t:
        lx = last["x"] if isinstance(last, dict) or hasattr(last, "__getitem__") else getattr(last, "x")
        ly = last["y"] if isinstance(last, dict) or hasattr(last, "__getitem__") else getattr(last, "y")
        return (int(lx), int(ly))

    return cur_pos


def detect_conflicts(robots: List[Any], current_tick: int) -> List[Dict[str, Any]]:
    """
    Scans all robot pairs and returns a list of detected conflicts.

    Trigger conditions:
    1. CELL_OVERLAP: Two robots are within a 2-cell Manhattan-distance radius
       of each other, AND their reserved paths show them occupying the SAME
       (x, y) cell within the next 2 ticks from current_tick (current_tick + 1 or current_tick + 2).
    2. SWAP_CONFLICT: Two robots' next-tick positions are each other's
       current positions (they would pass through each other in the same tick).

    Args:
        robots: List of Robot instances across the fleet.
        current_tick: Current simulation tick.

    Returns:
        List of conflict dictionaries conforming to:
        {
            "robot_ids": [str, str],
            "cell": {"x": int, "y": int},
            "tick": int,
            "type": "CELL_OVERLAP" | "SWAP_CONFLICT"
        }
    """
    conflicts: List[Dict[str, Any]] = []
    n = len(robots)
    if n < 2:
        return conflicts

    # Pre-extract positions for fast spatial filtering
    positions = [_get_robot_pos(r) for r in robots]

    for i in range(n):
        ra = robots[i]
        pos_a = positions[i]

        for j in range(i + 1, n):
            rb = robots[j]
            pos_b = positions[j]

            # 1. Spatial proximity filter: Manhattan distance <= 2
            manhattan_dist = abs(pos_a[0] - pos_b[0]) + abs(pos_a[1] - pos_b[1])
            if manhattan_dist > 2:
                continue

            # 2. Check SWAP_CONFLICT (SCHEMA.md §9 Swap rule)
            next_a = _get_pos_at_tick(ra, current_tick + 1, current_tick)
            next_b = _get_pos_at_tick(rb, current_tick + 1, current_tick)

            if next_a == pos_b and next_b == pos_a and pos_a != pos_b:
                conflicts.append({
                    "robot_ids": [ra.robot_id, rb.robot_id],
                    "cell": {"x": next_a[0], "y": next_a[1]},
                    "tick": current_tick + 1,
                    "type": "SWAP_CONFLICT",
                })
                continue

            # 3. Check CELL_OVERLAP across future horizon [current_tick + 1, current_tick + 2]
            conflict_found = False
            for dt in (1, 2):
                target_tick = current_tick + dt
                p_a = _get_pos_at_tick(ra, target_tick, current_tick)
                p_b = _get_pos_at_tick(rb, target_tick, current_tick)

                if p_a == p_b:
                    conflicts.append({
                        "robot_ids": [ra.robot_id, rb.robot_id],
                        "cell": {"x": p_a[0], "y": p_a[1]},
                        "tick": target_tick,
                        "type": "CELL_OVERLAP",
                    })
                    conflict_found = True
                    break

    return conflicts


def detect_peer_conflict(robot_a: Any, robot_b: Any, current_tick: int) -> Optional[Dict[str, Any]]:
    """
    Decentralized peer-to-peer conflict detection.
    Allows a single robot to evaluate whether it has an imminent spatial or swap
    conflict with a nearby peer within its 2-cell communication radius.
    """
    res = detect_conflicts([robot_a, robot_b], current_tick)
    return res[0] if res else None

