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


def _extract_goal(robot: Any, tasks: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
    """Extract the ultimate goal cell from robot's task, planned path, or current position."""
    if tasks and getattr(robot, "current_task_id", None) and robot.current_task_id in tasks:
        task = tasks[robot.current_task_id]
        status = getattr(task, "status", None)
        if status == "IN_PROGRESS" or getattr(task, "_pickup_done", False):
            return (int(task.dropoff[0]), int(task.dropoff[1]))
        elif status == "ASSIGNED":
            return (int(task.pickup[0]), int(task.pickup[1]))
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
    tasks: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Given a detected conflict, decides which robot yields and which proceeds.
    """
    id_a, id_b = conflict["robot_ids"][0], conflict["robot_ids"][1]
    robot_a = robots[id_a]
    robot_b = robots[id_b]
    pos_a = _extract_pos(robot_a)
    pos_b = _extract_pos(robot_b)

    conflict_cell_raw = conflict.get("cell")
    if isinstance(conflict_cell_raw, dict):
        conflict_cell = (int(conflict_cell_raw["x"]), int(conflict_cell_raw["y"]))
    elif isinstance(conflict_cell_raw, (tuple, list)):
        conflict_cell = (int(conflict_cell_raw[0]), int(conflict_cell_raw[1]))
    else:
        conflict_cell = pos_a

    # 1. Physical Occupancy Rule: if one robot is already stationary at the conflict cell,
    # it cannot yield in a way that allows another robot to walk through it. Approaching robot yields.
    # Note: In a SWAP_CONFLICT, neither robot is stationary — both are attempting to exchange cells.
    is_swap = conflict.get("type") == "SWAP_CONFLICT"
    a_at_cell = (pos_a == conflict_cell) and not is_swap
    b_at_cell = (pos_b == conflict_cell) and not is_swap

    if a_at_cell and not b_at_cell:
        winner, loser = robot_a, robot_b
    elif b_at_cell and not a_at_cell:
        winner, loser = robot_b, robot_a
    else:
        # 2 & 3. Priority Comparison and Lexicographical Tie-breaker
        score_a = float(robot_a.priority_score)
        score_b = float(robot_b.priority_score)

        if score_a > score_b:
            winner, loser = robot_a, robot_b
        elif score_b > score_a:
            winner, loser = robot_b, robot_a
        else:
            if robot_a.robot_id < robot_b.robot_id:
                winner, loser = robot_a, robot_b
            else:
                winner, loser = robot_b, robot_a

    # 4. Transition loser to CONFLICT_NEGOTIATING and increment wait count
    loser.state = RobotState.CONFLICT_NEGOTIATING
    if hasattr(loser, "wait_ticks_so_far"):
        loser.wait_ticks_so_far += 1
    elif hasattr(loser, "_wait_ticks"):
        loser._wait_ticks += 1

    # Purge ONLY the loser's reservations from the reservation table (Rule 5)
    stale_keys = [k for k, owner in list(reservation_table.items()) if owner == loser.robot_id]
    for k in stale_keys:
        del reservation_table[k]

    current_tick = conflict.get("current_tick")
    if current_tick is None:
        current_tick = getattr(loser, "last_updated_tick", None)
    if current_tick is None:
        current_tick = max(0, conflict.get("tick", 1) - 1)

    # 5. If winner intends to move into loser's current cell, winner holds 1 tick at its current cell to let yielding loser clear/turn
    start_pos = _extract_pos(loser)
    if getattr(winner, "path", None) and len(winner.path) > 1:
        w_pos = _extract_pos(winner)
        nxt = winner.path[1]
        nx = nxt["x"] if isinstance(nxt, dict) or hasattr(nxt, "__getitem__") else getattr(nxt, "x")
        ny = nxt["y"] if isinstance(nxt, dict) or hasattr(nxt, "__getitem__") else getattr(nxt, "y")
        if (int(nx), int(ny)) == start_pos:
            hold_step = {"x": w_pos[0], "y": w_pos[1], "t": current_tick + 1}
            shifted = [{"x": w_pos[0], "y": w_pos[1], "t": current_tick}, hold_step]
            for idx, step in enumerate(winner.path[1:], start=2):
                sx = step["x"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "x")
                sy = step["y"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "y")
                shifted.append({"x": sx, "y": sy, "t": current_tick + idx})
            winner.path = shifted

    # Ensure winner's planned path is explicitly locked in reservation_table so loser avoids it
    if getattr(winner, "path", None):
        for step in winner.path:
            wx = step["x"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "x")
            wy = step["y"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "y")
            wt = step["t"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "t")
            reservation_table[(int(wx), int(wy), int(wt))] = winner.robot_id
        # Extend hold reservation at winner's final waypoint so loser does not collide into stationary winner
        last_step = winner.path[-1]
        w_lx = last_step["x"] if isinstance(last_step, dict) or hasattr(last_step, "__getitem__") else getattr(last_step, "x")
        w_ly = last_step["y"] if isinstance(last_step, dict) or hasattr(last_step, "__getitem__") else getattr(last_step, "y")
        w_lt = last_step["t"] if isinstance(last_step, dict) or hasattr(last_step, "__getitem__") else getattr(last_step, "t")
        for extra in range(1, 31):
            key = (int(w_lx), int(w_ly), int(w_lt) + extra)
            reservation_table.setdefault(key, winner.robot_id)
    else:
        # If winner has no path, it is stationary at its current position
        w_cur = _extract_pos(winner)
        for extra in range(31):
            key = (int(w_cur[0]), int(w_cur[1]), current_tick + extra)
            reservation_table.setdefault(key, winner.robot_id)

    # Compute new path for loser
    goal_pos = _extract_goal(loser, tasks=tasks)

    # Call find_path_fn adaptively
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

    if not new_path:
        # Loser cannot advance immediately: hold position for current and next tick
        new_path = [
            {"x": start_pos[0], "y": start_pos[1], "t": current_tick},
            {"x": start_pos[0], "y": start_pos[1], "t": current_tick + 1},
        ]
    elif len(new_path) == 1 and new_path[0]["t"] == current_tick:
        new_path.append({"x": new_path[0]["x"], "y": new_path[0]["y"], "t": current_tick + 1})

    # Register loser's new path in reservation_table
    for step in new_path:
        st_x = step["x"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "x")
        st_y = step["y"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "y")
        st_t = step["t"] if isinstance(step, dict) or hasattr(step, "__getitem__") else getattr(step, "t")
        reservation_table[(int(st_x), int(st_y), int(st_t))] = loser.robot_id

    # Extend hold reservation at final waypoint so oncoming robots do not collide into stationary robot
    if new_path:
        last_step = new_path[-1]
        lx = last_step["x"] if isinstance(last_step, dict) or hasattr(last_step, "__getitem__") else getattr(last_step, "x")
        ly = last_step["y"] if isinstance(last_step, dict) or hasattr(last_step, "__getitem__") else getattr(last_step, "y")
        lt = last_step["t"] if isinstance(last_step, dict) or hasattr(last_step, "__getitem__") else getattr(last_step, "t")
        for extra in range(1, 31):
            key = (int(lx), int(ly), int(lt) + extra)
            reservation_table.setdefault(key, loser.robot_id)

    # Assign new path to loser
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
        "cell": {"x": conflict_cell[0], "y": conflict_cell[1]},
    }


def resolve_peer_conflict(
    conflict: Dict[str, Any],
    robot_a: Any,
    robot_b: Any,
    reservation_table: Dict[Tuple[int, int, int], str],
    find_path_fn: Callable[..., List[Dict[str, Any]]],
    tasks: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Decentralized peer-to-peer conflict resolution.
    Directly resolves a detected conflict between two peer robots without needing
    a central fleet dictionary.
    """
    robots_dict = {robot_a.robot_id: robot_a, robot_b.robot_id: robot_b}
    return resolve_conflict(
        conflict=conflict,
        robots=robots_dict,
        reservation_table=reservation_table,
        find_path_fn=find_path_fn,
        tasks=tasks,
    )

