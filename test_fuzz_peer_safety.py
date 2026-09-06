"""
test_fuzz_peer_safety.py
Property-based fuzz testing harness for decentralized peer-to-peer conflict safety.

Directly imports and tests Member 3's peer-to-peer conflict resolution functions:
  - detect_peer_conflict()
  - resolve_peer_conflict()
as used by robot_node.py, NOT the centralized versions.

Proves:
  1. No two robots ever share a cell (no cell / vertex collisions).
  2. No head-on swap conflict occurs (no swapping positions in a single tick).

Configured with @settings(max_examples=300, deadline=None).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from hypothesis import given, settings, strategies as st

# Setup import paths
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "testing"))

from grid import WarehouseGrid
from pathfinder import find_path
from reservations import reserve_path, release_reservations, prune_past
from priority import calculate_priority_score
from conflict_detector import detect_peer_conflict
from arbitration import resolve_peer_conflict
from models import Heading, Robot, RobotState, Task
from full_integration_test import get_static_shelves

# ---------------------------------------------------------------------------
# Map Initialization
# ---------------------------------------------------------------------------
STATIC_OBSTACLES = get_static_shelves()
WAREHOUSE_GRID = WarehouseGrid(obstacles=STATIC_OBSTACLES, width=30, height=30)
OPEN_CELLS: List[Tuple[int, int]] = [
    (x, y)
    for x in range(WAREHOUSE_GRID.width)
    for y in range(WAREHOUSE_GRID.height)
    if WAREHOUSE_GRID.is_free((x, y))
]

# Find horizontal and vertical corridor segments to bias towards head-on/crossing conflicts
CORRIDOR_SEGMENTS: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

# Horizontal open segments (e.g. y in [6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24])
for y in [6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23, 24]:
    for x1 in range(2, 20):
        x2 = x1 + random.randint(3, 8)
        if x2 < 28 and all(WAREHOUSE_GRID.is_free((x, y)) for x in range(x1, x2 + 1)):
            CORRIDOR_SEGMENTS.append(((x1, y), (x2, y)))

# Vertical open segments along vertical aisles (x in [7, 14, 21])
for x in [7, 14, 21]:
    for y1 in range(1, 22):
        y2 = y1 + random.randint(3, 8)
        if y2 < 29 and all(WAREHOUSE_GRID.is_free((x, y)) for y in range(y1, y2 + 1)):
            CORRIDOR_SEGMENTS.append(((x, y1), (x, y2)))


# ---------------------------------------------------------------------------
# Hypothesis Strategies: High Conflict Bias
# ---------------------------------------------------------------------------
@st.composite
def peer_conflict_scenario_strategy(draw: Any) -> Dict[str, Any]:
    """
    Generates scenarios biased heavily toward collision courses:
      - Head-on / swap conflicts along corridors
      - Perpendicular intersection crossings
      - Clustered small groups (2-4 robots)
    """
    mode = draw(st.sampled_from(["head_on", "crossing", "cluster"]))
    
    robots_spec = []
    
    if mode == "head_on" and CORRIDOR_SEGMENTS:
        # Pick a corridor segment: Robot 1 goes p1 -> p2, Robot 2 goes p2 -> p1
        seg = draw(st.sampled_from(CORRIDOR_SEGMENTS))
        p1, p2 = seg[0], seg[1]
        
        urg1 = draw(st.integers(min_value=1, max_value=5))
        urg2 = draw(st.integers(min_value=1, max_value=5))
        batt1 = draw(st.floats(min_value=20.0, max_value=100.0))
        batt2 = draw(st.floats(min_value=20.0, max_value=100.0))
        
        robots_spec.append({
            "robot_id": "AMR-01",
            "start": p1,
            "goal": p2,
            "urgency": urg1,
            "battery_pct": round(batt1, 1),
        })
        robots_spec.append({
            "robot_id": "AMR-02",
            "start": p2,
            "goal": p1,
            "urgency": urg2,
            "battery_pct": round(batt2, 1),
        })
        
        # Optionally add a 3rd robot crossing nearby
        if draw(st.booleans()):
            open_nearby = [c for c in OPEN_CELLS if abs(c[0] - p1[0]) + abs(c[1] - p1[1]) <= 6 and c not in (p1, p2)]
            if len(open_nearby) >= 2:
                s3, g3 = draw(st.lists(st.sampled_from(open_nearby), min_size=2, max_size=2, unique=True))
                robots_spec.append({
                    "robot_id": "AMR-03",
                    "start": s3,
                    "goal": g3,
                    "urgency": draw(st.integers(min_value=1, max_value=5)),
                    "battery_pct": round(draw(st.floats(min_value=20.0, max_value=100.0)), 1),
                })

    elif mode == "crossing":
        # Perpendicular crossing at an intersection (e.g. x=7, 14, 21 and horizontal aisles)
        ix = draw(st.sampled_from([7, 14, 21]))
        iy = draw(st.sampled_from([6, 11, 16, 21]))
        
        # Horizontal traveler through (ix, iy)
        h_start = (max(0, ix - draw(st.integers(min_value=2, max_value=4))), iy)
        h_goal = (min(29, ix + draw(st.integers(min_value=2, max_value=4))), iy)
        
        # Vertical traveler through (ix, iy)
        v_start = (ix, max(0, iy - draw(st.integers(min_value=2, max_value=4))))
        v_goal = (ix, min(29, iy + draw(st.integers(min_value=2, max_value=4))))
        
        # Ensure all starts and goals are distinct and free
        positions = [h_start, h_goal, v_start, v_goal]
        if len(set(positions)) == 4 and all(WAREHOUSE_GRID.is_free(p) for p in positions):
            robots_spec.append({
                "robot_id": "AMR-01",
                "start": h_start,
                "goal": h_goal,
                "urgency": draw(st.integers(min_value=1, max_value=5)),
                "battery_pct": round(draw(st.floats(min_value=20.0, max_value=100.0)), 1),
            })
            robots_spec.append({
                "robot_id": "AMR-02",
                "start": v_start,
                "goal": v_goal,
                "urgency": draw(st.integers(min_value=1, max_value=5)),
                "battery_pct": round(draw(st.floats(min_value=20.0, max_value=100.0)), 1),
            })

    if len(robots_spec) < 2:
        # Fallback to clustered small group
        n = draw(st.integers(min_value=2, max_value=4))
        chosen = draw(st.lists(st.sampled_from(OPEN_CELLS), min_size=2 * n, max_size=2 * n, unique=True))
        starts = chosen[:n]
        goals = chosen[n:]
        for i in range(n):
            robots_spec.append({
                "robot_id": f"AMR-{i+1:02d}",
                "start": starts[i],
                "goal": goals[i],
                "urgency": draw(st.integers(min_value=1, max_value=5)),
                "battery_pct": round(draw(st.floats(min_value=20.0, max_value=100.0)), 1),
            })

    return {"robots": robots_spec}


# ---------------------------------------------------------------------------
# Decentralized Peer Simulation Tick Loop
# ---------------------------------------------------------------------------
def run_peer_simulation_scenario(scenario: Dict[str, Any], max_ticks: int = 35) -> None:
    grid = WAREHOUSE_GRID
    robots: Dict[str, Robot] = {}
    tasks: Dict[str, Task] = {}
    local_reservations: Dict[str, Dict[Tuple[int, int, int], str]] = {}
    
    HOLD = 20

    for spec in scenario["robots"]:
        rid = spec["robot_id"]
        tid = f"TASK-{rid}"
        task = Task(
            task_id=tid,
            pickup=spec["start"],
            dropoff=spec["goal"],
            urgency=spec["urgency"],
            created_tick=0,
            assigned_robot_id=rid,
            status="IN_PROGRESS",
        )
        robot = Robot(
            robot_id=rid,
            position=spec["start"],
            heading=Heading.NORTH,
            state=RobotState.EN_ROUTE,
            battery_pct=spec["battery_pct"],
            current_task_id=tid,
            path=[],
            priority_score=0.0,
            wait_ticks_so_far=0,
            last_updated_tick=0,
        )
        robots[rid] = robot
        tasks[tid] = task
        local_reservations[rid] = {}

    # Initial path planning per robot
    for rid, robot in robots.items():
        task = tasks[robot.current_task_id]
        dist = robot.distance_to_goal()
        robot.priority_score = calculate_priority_score(robot, task, dist)
        
        path = find_path(
            start=robot.position,
            goal=task.dropoff,
            current_tick=0,
            reservation_table=local_reservations[rid],
            grid=grid,
        )
        if path:
            robot.path = path
            reserve_path(path, robot.robot_id, local_reservations[rid], hold_ticks_at_goal=HOLD)
        else:
            robot.path = [{"x": robot.position[0], "y": robot.position[1], "t": 0}]
            reserve_path(robot.path, robot.robot_id, local_reservations[rid], hold_ticks_at_goal=HOLD)

    # Pathfinder callback for peer conflict resolution
    def _pathfinder_fn(start, goal, cur_tick, res_table, robot_id=None, **kwargs):
        return find_path(
            start=start,
            goal=goal,
            current_tick=cur_tick,
            reservation_table=res_table,
            grid=grid,
        )

    # Tick Loop
    all_robot_ids = sorted(robots.keys())
    for tick in range(max_ticks):
        if all(r.state == RobotState.IDLE for r in robots.values()):
            break

        prev_positions = {r.robot_id: r.position for r in robots.values()}

        # 1. Update priority scores for all active robots
        for robot in robots.values():
            if robot.state == RobotState.EN_ROUTE:
                dist = robot.distance_to_goal()
                task = tasks[robot.current_task_id]
                robot.priority_score = calculate_priority_score(robot, task, dist)

        # 2. Replan path if robot was waiting or path exhausted
        for robot in robots.values():
            if robot.state == RobotState.EN_ROUTE and (not robot.path or len(robot.path) <= 1 or robot.wait_ticks_so_far > 0):
                task = tasks[robot.current_task_id]
                release_reservations(robot.robot_id, local_reservations[robot.robot_id])
                re_path = find_path(
                    start=robot.position,
                    goal=task.dropoff,
                    current_tick=tick,
                    reservation_table=local_reservations[robot.robot_id],
                    robot_id=robot.robot_id,
                    grid=grid,
                )
                if re_path and len(re_path) > 1:
                    robot.path = re_path
                    reserve_path(re_path, robot.robot_id, local_reservations[robot.robot_id], hold_ticks_at_goal=HOLD)
                elif robot.wait_ticks_so_far >= 3:
                    # Deadlock breaker: search adjacent free nook to step aside
                    rx, ry = robot.position
                    candidate_nooks = [(rx, ry - 1), (rx, ry + 1), (rx + 1, ry), (rx - 1, ry)]
                    other_positions = {r.position for r in robots.values() if r.robot_id != robot.robot_id}
                    nook_path = None
                    for cand in candidate_nooks:
                        if 0 <= cand[0] < grid.width and 0 <= cand[1] < grid.height:
                            if grid.is_free(cand) and cand not in other_positions:
                                n_p = find_path(robot.position, cand, tick, local_reservations[robot.robot_id], robot_id=robot.robot_id, grid=grid)
                                if n_p and len(n_p) > 1:
                                    nook_path = n_p
                                    break
                    if nook_path:
                        robot.path = nook_path
                        reserve_path(nook_path, robot.robot_id, local_reservations[robot.robot_id], hold_ticks_at_goal=HOLD)
                    else:
                        robot.path = [{"x": robot.position[0], "y": robot.position[1], "t": tick}, {"x": robot.position[0], "y": robot.position[1], "t": tick + 1}]
                        reserve_path(robot.path, robot.robot_id, local_reservations[robot.robot_id], hold_ticks_at_goal=HOLD)
                else:
                    robot.path = [{"x": robot.position[0], "y": robot.position[1], "t": tick}, {"x": robot.position[0], "y": robot.position[1], "t": tick + 1}]
                    reserve_path(robot.path, robot.robot_id, local_reservations[robot.robot_id], hold_ticks_at_goal=HOLD)

        # 3. Share paths among peers (simulating peer broadcast & reservation exchange)
        for rid in all_robot_ids:
            # Ensure own cell is reserved if stationary/idle
            r_self = robots[rid]
            if r_self.state == RobotState.IDLE or not r_self.path or len(r_self.path) <= 1:
                for dt in range(HOLD):
                    local_reservations[rid][(r_self.position[0], r_self.position[1], tick + dt)] = rid

            for other_id in all_robot_ids:
                if other_id != rid:
                    # Clean previous reservations from other_id in rid's local table
                    for k in [k for k, v in list(local_reservations[rid].items()) if v == other_id]:
                        del local_reservations[rid][k]
                    other_r = robots[other_id]
                    if other_r.path and len(other_r.path) > 1:
                        for p in other_r.path[:8]:
                            local_reservations[rid][(int(p["x"]), int(p["y"]), int(p["t"]))] = other_id
                        last_p = other_r.path[min(len(other_r.path) - 1, 7)]
                        for dt in range(1, HOLD):
                            local_reservations[rid][(int(last_p["x"]), int(last_p["y"]), int(last_p["t"]) + dt)] = other_id
                    else:
                        for dt in range(HOLD):
                            local_reservations[rid][(other_r.position[0], other_r.position[1], tick + dt)] = other_id

        # 4. Peer-to-Peer Conflict Detection & Resolution (DIRECT CALLS)
        # Directly call detect_peer_conflict and resolve_peer_conflict for all peer pairs
        for i in range(len(all_robot_ids)):
            for j in range(i + 1, len(all_robot_ids)):
                ra = robots[all_robot_ids[i]]
                rb = robots[all_robot_ids[j]]

                if ra.state == RobotState.IDLE and rb.state == RobotState.IDLE:
                    continue

                # Call detect_peer_conflict directly (the exact peer function used by robot nodes)
                conflict = detect_peer_conflict(ra, rb, current_tick=tick)
                if conflict:
                    conflict["current_tick"] = tick
                    # Call resolve_peer_conflict directly
                    shared_res_view = dict(local_reservations[ra.robot_id])
                    shared_res_view.update(local_reservations[rb.robot_id])

                    res = resolve_peer_conflict(
                        conflict=conflict,
                        robot_a=ra,
                        robot_b=rb,
                        reservation_table=shared_res_view,
                        find_path_fn=_pathfinder_fn,
                        tasks=tasks,
                    )
                    # Update local reservations with resolution result
                    local_reservations[ra.robot_id] = dict(shared_res_view)
                    local_reservations[rb.robot_id] = dict(shared_res_view)

        # 5. Advance Robots along their assigned paths
        for robot in robots.values():
            if robot.state == RobotState.IDLE or not robot.path:
                continue

            if len(robot.path) > 1:
                next_step = robot.path[1]
                next_pos = (int(next_step["x"]), int(next_step["y"]))

                if next_pos != robot.position:
                    dx = next_pos[0] - robot.position[0]
                    dy = next_pos[1] - robot.position[1]
                    if dx > 0: robot.heading = Heading.EAST
                    elif dx < 0: robot.heading = Heading.WEST
                    elif dy > 0: robot.heading = Heading.SOUTH
                    elif dy < 0: robot.heading = Heading.NORTH

                    robot.position = next_pos
                    robot.path = robot.path[1:]
                    robot.wait_ticks_so_far = 0
                else:
                    robot.path = robot.path[1:]
                    robot.wait_ticks_so_far += 1
            else:
                robot.wait_ticks_so_far += 1

            robot.last_updated_tick = tick

            # Handle destination reached
            task = tasks[robot.current_task_id]
            if robot.position == task.dropoff:
                task.status = "COMPLETED"
                robot.state = RobotState.IDLE
                robot.path = []

        # Prune reservations
        for rid in all_robot_ids:
            prune_past(local_reservations[rid], tick)

        # =====================================================================
        # ASSERTION 1: No two robots occupy the same (x, y) cell at the same tick
        # =====================================================================
        all_positions = [r.position for r in robots.values()]
        assert len(all_positions) == len(set(all_positions)), (
            f"CELL COLLISION detected at tick {tick}! Positions: {all_positions}"
        )

        # =====================================================================
        # ASSERTION 2: No swap collision occurred between tick-1 and tick
        # =====================================================================
        for i in range(len(all_robot_ids)):
            for j in range(i + 1, len(all_robot_ids)):
                id_a = all_robot_ids[i]
                id_b = all_robot_ids[j]
                p_a_prev, p_a_now = prev_positions[id_a], robots[id_a].position
                p_b_prev, p_b_now = prev_positions[id_b], robots[id_b].position

                is_swap = (p_a_prev == p_b_now and p_b_prev == p_a_now and p_a_prev != p_b_prev)
                assert not is_swap, (
                    f"SWAP COLLISION detected at tick {tick} between {id_a} and {id_b}! "
                    f"{id_a}: {p_a_prev} -> {p_a_now}, {id_b}: {p_b_prev} -> {p_b_now}"
                )


# ---------------------------------------------------------------------------
# Property-Based Fuzz Test: 300 Examples
# ---------------------------------------------------------------------------
@settings(max_examples=300, deadline=None)
@given(scenario=peer_conflict_scenario_strategy())
def test_fuzz_peer_safety(scenario: Dict[str, Any]):
    """
    Hypothesis Property Test:
    Generates 300 randomized, conflict-heavy scenarios exercising the decentralized
    peer-to-peer conflict detection (detect_peer_conflict) and arbitration (resolve_peer_conflict).
    Asserts zero cell collisions and zero swap collisions across all ticks.
    """
    run_peer_simulation_scenario(scenario, max_ticks=30)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
