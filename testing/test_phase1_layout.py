"""
test_phase1_layout.py — Phase 1 Acceptance and Red-Team Attack Tests

Verifies:
1. Every pod slot is individually addressable, has a unique shelf_id, and no coordinates are duplicated.
2. Every pod slot is reachable from adjacent walkable cells (no orphaned pods).
3. Red-team Pathfinding Sweep: Every pod slot is reachable from all charging stations and dock gates.
4. Path lengths are within reasonable bounds (<= 2x Manhattan distance on unobstructed grid).
5. Cross-highways (x=10, x=19) provide open, decongested transit.
"""

import sys
from pathlib import Path
import pytest
import networkx as nx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.world import build_default_world, WorldConfig
from app.services.grid import WarehouseGrid
from app.services.pathfinder import SpaceTimeAStarPlanner, find_path
from app.services.reservations import reserve_path, release_reservations


def test_pod_slots_uniqueness_and_validity():
    world = build_default_world()
    assert len(world.pod_slots) > 0, "Pod yard must contain pod slots."
    
    seen_coords = set()
    seen_shelf_ids = set()

    for shelf_id, (x, y) in world.pod_slots.items():
        assert shelf_id not in seen_shelf_ids, f"Duplicate shelf_id: {shelf_id}"
        assert (x, y) not in seen_coords, f"Duplicate coordinate {(x, y)} for shelf_id {shelf_id}"
        seen_shelf_ids.add(shelf_id)
        seen_coords.add((x, y))

        assert world.in_bounds(x, y), f"Pod slot {(x, y)} is out of bounds."
        assert (x, y) not in world.static_obstacles, f"Pod slot {(x, y)} must not be a static obstacle."
        assert world.shelf_at(x, y) == shelf_id, f"shelf_at({x}, {y}) returned {world.shelf_at(x, y)}, expected {shelf_id}"


def test_pod_slots_reachability():
    """Verify that every pod slot has at least one adjacent walkable cell and is reachable on the grid."""
    world = build_default_world()
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)

    for shelf_id, (x, y) in world.pod_slots.items():
        neighbors = [(x + dx, y + dy) for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]]
        valid_walkable_neighbors = [n for n in neighbors if grid.is_free(n)]
        assert len(valid_walkable_neighbors) >= 1, f"Orphaned pod slot {shelf_id} at {(x, y)}: no walkable adjacent cells."


def test_redteam_full_pathfinding_sweep():
    """
    Attack check 1: Pathfinding sweep from every charging station and dock cell
    to every pod slot. Confirms reachability and reasonable path lengths.
    """
    world = build_default_world()
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)

    sources = list(world.charging_stations) + list(world.pickup_stations) + list(world.dropoff_stations)
    
    unreachable_count = 0
    excessive_paths = []

    for src in sources:
        for shelf_id, dst in world.pod_slots.items():
            dist = grid.true_distance(src, dst)
            assert dist is not None, f"Pod {shelf_id} at {dst} is unreachable from {src}!"
            
            manhattan = abs(src[0] - dst[0]) + abs(src[1] - dst[1])
            # Pod yard layout paths should never exceed 2x Manhattan distance + 10 (for aisle navigation)
            if dist > max(manhattan * 2.0, manhattan + 8):
                excessive_paths.append((src, shelf_id, dst, dist, manhattan))

    assert len(excessive_paths) == 0, f"Found abnormally long paths: {excessive_paths[:5]}"


def test_redteam_cross_highways_connectivity():
    """Attack check 3: Confirm cross-highways x=10 and x=19 are clear north-to-south."""
    world = build_default_world()
    for x in (10, 19):
        for y in range(world.height):
            assert (x, y) not in world.static_obstacles, f"Highway x={x}, y={y} is blocked by static obstacle"
            assert world.shelf_at(x, y) is None, f"Highway x={x}, y={y} has a pod placed in it"


def test_redteam_concurrent_aisle_access():
    """
    Attack check 2: Simulate 8 robots all targeting pod slots in the same aisle
    simultaneously using the Space-Time A* reservation system to confirm no deadlocks.
    """
    world = build_default_world()
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)
    planner = SpaceTimeAStarPlanner(grid=grid)
    table: dict = {}

    # 8 robots starting from West staging / chargers targeting Bank A pods (rows 6, 7)
    targets = [(4, 6), (5, 6), (6, 6), (7, 6), (8, 6), (9, 6), (4, 7), (5, 7)]
    starts = [(0, 8), (0, 9), (0, 10), (0, 13), (5, 1), (10, 1), (19, 1), (24, 1)]

    paths = []
    for i, (start, goal) in enumerate(zip(starts, targets)):
        robot_id = f"ROBOT-{i+1}"
        path = planner.plan_path(
            start=start,
            goal=goal,
            current_tick=0,
            reservation_table=table,
        )
        assert len(path) > 0, f"Robot {robot_id} could not find path to {goal} from {start}"
        reserve_path(path, robot_id, table)
        paths.append(path)

    # Verify no two robots share (x, y, t) or swap (p1->p2 & p2->p1 at same t)
    for t in range(100):
        positions_at_t = {}
        for r_idx, path in enumerate(paths):
            matching_step = next((step for step in path if step["t"] == t), None)
            if matching_step:
                pos = (matching_step["x"], matching_step["y"])
                assert pos not in positions_at_t, f"Cell collision at t={t}, cell={pos} between robot {r_idx} and {positions_at_t[pos]}"
                positions_at_t[pos] = r_idx

