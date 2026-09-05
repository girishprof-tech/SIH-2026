"""
test_multi_robot_stress.py — sanity check at the scale SCHEMA.md's open
items suggest stress-testing with ("15+" robots), using the standard
prioritized-planning pattern: plan robots one at a time in priority order,
each one reserving into a shared table before the next one plans.

This isn't a unit test of a single function so much as an integration smoke
test that the contract (find_path + reserve_path) composes correctly at
fleet scale with zero collisions.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner
from reservations import reserve_path


def test_20_robots_no_collisions_no_swaps():
    random.seed(7)
    width, height = 30, 30
    obstacles = {(5, y) for y in range(0, 20)} | {(20, y) for y in range(10, 30)}
    grid = WarehouseGrid(obstacles=list(obstacles), width=width, height=height)
    planner = SpaceTimeAStarPlanner(grid, horizon_padding=80)

    free_cells = [
        (x, y)
        for x in range(width)
        for y in range(height)
        if (x, y) not in obstacles
    ]
    random.shuffle(free_cells)

    num_robots = 20
    starts = free_cells[:num_robots]
    goals = free_cells[num_robots:2 * num_robots]

    reservation_table = {}
    all_paths = {}

    for i in range(num_robots):
        robot_id = f"AMR-{i:02d}"
        path = planner.plan_path(
            starts[i], goals[i], current_tick=0,
            reservation_table=reservation_table, robot_id=robot_id,
        )
        assert path, f"{robot_id} should find a path from {starts[i]} to {goals[i]}"
        reserve_path(path, robot_id, reservation_table)
        all_paths[robot_id] = path

    # 1. No two robots ever occupy the same (x, y, t).
    cell_owner = {}
    for robot_id, path in all_paths.items():
        for step in path:
            key = (step["x"], step["y"], step["t"])
            assert key not in cell_owner, (
                f"collision at {key} between {cell_owner.get(key)} and {robot_id}"
            )
            cell_owner[key] = robot_id

    # 2. No swap: for every pair of robots, no (A: p1->p2, B: p2->p1) at the
    # same tick transition.
    by_robot_positions = {
        robot_id: {step["t"]: (step["x"], step["y"]) for step in path}
        for robot_id, path in all_paths.items()
    }
    ids = list(by_robot_positions)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            common_ticks = set(by_robot_positions[a]) & set(by_robot_positions[b])
            for t in common_ticks:
                if t + 1 not in by_robot_positions[a] or t + 1 not in by_robot_positions[b]:
                    continue
                a_now, a_next = by_robot_positions[a][t], by_robot_positions[a][t + 1]
                b_now, b_next = by_robot_positions[b][t], by_robot_positions[b][t + 1]
                swapped = a_now == b_next and a_next == b_now and a_now != a_next
                assert not swapped, f"swap violation between {a} and {b} at tick {t}"

    # 3. No obstacle was ever routed through.
    for robot_id, path in all_paths.items():
        for step in path:
            assert (step["x"], step["y"]) not in obstacles
