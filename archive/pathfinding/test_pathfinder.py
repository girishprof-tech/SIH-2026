"""
test_pathfinder.py — pytest suite for the Space-Time A* pathfinder.
Owner: Member 2 — Core Algorithm Engineer.  SIH26123.

Run with:  pytest -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner, find_path, configure_default_grid
from reservations import reserve_path, release_reservations, prune_past


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_grid():
    return WarehouseGrid(obstacles=[], width=30, height=30)


@pytest.fixture
def wall_grid():
    # Vertical wall x=5, y=0..10, with a gap at y=11 to route through.
    obstacles = [(5, y) for y in range(0, 11)]
    return WarehouseGrid(obstacles=obstacles, width=30, height=30)


@pytest.fixture
def planner(empty_grid):
    return SpaceTimeAStarPlanner(empty_grid)


# ---------------------------------------------------------------------------
# basic correctness
# ---------------------------------------------------------------------------

class TestBasicPathfinding:
    def test_straight_line_no_obstacles(self, planner):
        path = planner.plan_path((0, 0), (5, 0), current_tick=0, reservation_table={})
        assert path, "expected a path"
        assert path[0] == {"x": 0, "y": 0, "t": 0}
        assert path[-1]["x"] == 5 and path[-1]["y"] == 0
        # Every step must be either a move to an orthogonal neighbour, or a
        # turn/wait in place — never a diagonal jump.
        for a, b in zip(path, path[1:]):
            dx, dy = abs(a["x"] - b["x"]), abs(a["y"] - b["y"])
            assert (dx, dy) in {(0, 0), (1, 0), (0, 1)}
            assert b["t"] == a["t"] + 1

    def test_start_equals_goal(self, planner):
        path = planner.plan_path((3, 3), (3, 3), current_tick=42, reservation_table={})
        assert path == [{"x": 3, "y": 3, "t": 42}]

    def test_path_stays_in_bounds(self, planner):
        path = planner.plan_path((0, 0), (29, 29), current_tick=0, reservation_table={})
        assert path
        for step in path:
            assert 0 <= step["x"] < 30
            assert 0 <= step["y"] < 30

    def test_out_of_bounds_start_returns_empty(self, planner):
        assert planner.plan_path((-1, 0), (5, 5), 0, {}) == []
        assert planner.plan_path((0, 0), (30, 5), 0, {}) == []


# ---------------------------------------------------------------------------
# obstacle avoidance
# ---------------------------------------------------------------------------

class TestObstacles:
    def test_routes_around_wall(self, wall_grid):
        planner = SpaceTimeAStarPlanner(wall_grid)
        path = planner.plan_path((0, 5), (10, 5), current_tick=0, reservation_table={})
        assert path
        visited = {(s["x"], s["y"]) for s in path}
        assert not visited & wall_grid.obstacles, "path must not cross the wall"
        assert (10, 5) in visited

    def test_goal_on_obstacle_is_unreachable(self, wall_grid):
        planner = SpaceTimeAStarPlanner(wall_grid)
        path = planner.plan_path((0, 0), (5, 5), current_tick=0, reservation_table={})
        assert path == []

    def test_start_on_obstacle_is_unreachable(self, wall_grid):
        planner = SpaceTimeAStarPlanner(wall_grid)
        path = planner.plan_path((5, 5), (0, 0), current_tick=0, reservation_table={})
        assert path == []

    def test_sealed_off_goal_returns_empty(self):
        # Box the goal in on all 4 sides -> genuinely unreachable.
        obstacles = [(6, 5), (4, 5), (5, 4), (5, 6)]
        grid = WarehouseGrid(obstacles=obstacles, width=30, height=30)
        planner = SpaceTimeAStarPlanner(grid)
        assert planner.plan_path((0, 0), (5, 5), 0, {}) == []


# ---------------------------------------------------------------------------
# reservation table / multi-robot conflict avoidance
# ---------------------------------------------------------------------------

class TestReservations:
    def test_avoids_reserved_cell(self, planner):
        # Block the direct route's midpoint cell at the exact tick a naive
        # planner would land on it.
        reservation_table = {(3, 0, 3): "AMR-99"}
        path = planner.plan_path((0, 0), (6, 0), current_tick=0, reservation_table=reservation_table)
        assert path
        # our path must never occupy a cell/tick reserved by another robot
        assert (3, 0, 3) not in {(s["x"], s["y"], s["t"]) for s in path}

    def test_own_robot_id_ignores_its_own_reservation(self, planner):
        # A robot re-planning should not be blocked by its own old claim.
        reservation_table = {}
        first = planner.plan_path((0, 0), (6, 0), 0, reservation_table, robot_id="AMR-01")
        reserve_path(first, "AMR-01", reservation_table)
        # Replan the *same* robot over the *same* reservation table/tick — must
        # still succeed because we pass robot_id="AMR-01" again.
        second = planner.plan_path((0, 0), (6, 0), 0, reservation_table, robot_id="AMR-01")
        assert second != []

    def test_foreign_reservation_blocks_replan(self, planner):
        reservation_table = {}
        first = planner.plan_path((0, 0), (6, 0), 0, reservation_table, robot_id="AMR-01")
        reserve_path(first, "AMR-01", reservation_table)
        # A *different* robot, starting elsewhere, whose route crosses
        # AMR-01's exact reserved cells/ticks must be forced to detour or
        # wait, not collide with them.
        second = planner.plan_path((0, 3), (6, 0), 0, reservation_table, robot_id="AMR-02")
        assert second, "AMR-02 should still find *some* path (wait/detour)"
        first_cells = {(s["x"], s["y"], s["t"]) for s in first}
        second_cells = {(s["x"], s["y"], s["t"]) for s in second}
        assert not (first_cells & second_cells), "must not occupy AMR-01's reserved cells at the same ticks"

    def test_swap_rule_is_never_violated(self, planner):
        # Robot B is reserved at (6,0) at t=0 and (5,0) at t=1 (B moving west).
        # Our robot A starts at (5,0) heading east toward (6,0): a naive
        # planner might try to swap with B. Verify our planner never does.
        reservation_table = {(6, 0, 0): "AMR-B", (5, 0, 1): "AMR-B"}
        path = planner.plan_path((5, 0), (6, 0), current_tick=0, reservation_table=reservation_table, robot_id="AMR-A")
        # Either A finds an alternate-timed path, or (if truly boxed by the
        # horizon) returns []. What it must NEVER do is have a step from
        # (5,0)@t=0 -> (6,0)@t=1, since that IS the forbidden swap.
        if path:
            for a, b in zip(path, path[1:]):
                is_the_swap_move = (
                    a["x"] == 5 and a["y"] == 0 and a["t"] == 0
                    and b["x"] == 6 and b["y"] == 0 and b["t"] == 1
                )
                assert not is_the_swap_move

    def test_prune_past_removes_old_entries(self, planner):
        reservation_table = {(0, 0, 1): "AMR-01", (0, 0, 10): "AMR-02"}
        removed = prune_past(reservation_table, current_tick=5)
        assert removed == 1
        assert (0, 0, 1) not in reservation_table
        assert (0, 0, 10) in reservation_table

    def test_release_reservations_clears_only_that_robot(self, planner):
        reservation_table = {(0, 0, 1): "AMR-01", (1, 1, 2): "AMR-02"}
        release_reservations("AMR-01", reservation_table)
        assert (0, 0, 1) not in reservation_table
        assert (1, 1, 2) in reservation_table


# ---------------------------------------------------------------------------
# turn cost
# ---------------------------------------------------------------------------

class TestTurnCost:
    def test_no_turn_needed_is_cheaper_than_forced_turn(self, planner):
        # Facing EAST already, moving to (5,0): should take exactly 5 ticks
        # (one per cell, no turns).
        path = planner.plan_path((0, 0), (5, 0), 0, {}, start_heading="EAST")
        assert path[-1]["t"] - path[0]["t"] == 5

    def test_perpendicular_start_heading_costs_extra_tick(self, planner):
        # Facing NORTH but goal is straight east: must spend 1 tick turning.
        path = planner.plan_path((0, 0), (5, 0), 0, {}, start_heading="NORTH")
        assert path[-1]["t"] - path[0]["t"] == 6  # 5 moves + 1 turn

    def test_inferred_heading_avoids_spurious_turn_cost(self, planner):
        # No start_heading given -> planner should infer a heading toward the
        # goal so no wasted turn tick is charged for a direct straight path.
        path = planner.plan_path((0, 0), (5, 0), 0, {})
        assert path[-1]["t"] - path[0]["t"] == 5


# ---------------------------------------------------------------------------
# contract-signature compliance (module-level find_path)
# ---------------------------------------------------------------------------

class TestContractSignature:
    def test_positional_call_matches_schema_signature(self):
        configure_default_grid(obstacles=[])
        path = find_path((0, 0), (4, 4), 0, {})
        assert path
        assert path[0] == {"x": 0, "y": 0, "t": 0}
        assert path[-1]["x"] == 4 and path[-1]["y"] == 4

    def test_returns_list_of_dicts_with_correct_keys(self):
        configure_default_grid(obstacles=[])
        path = find_path((0, 0), (3, 0), 0, {})
        for step in path:
            assert set(step.keys()) == {"x", "y", "t"}
            assert isinstance(step["x"], int)
            assert isinstance(step["y"], int)
            assert isinstance(step["t"], int)

    def test_schema_example_obstacles_load_correctly(self):
        static_map = {
            "obstacles": [{"x": 5, "y": 5}, {"x": 5, "y": 6}, {"x": 5, "y": 7}],
            "charging_stations": [{"x": 0, "y": 0}, {"x": 29, "y": 29}],
            "pickup_stations": [{"x": 4, "y": 22}],
        }
        grid = WarehouseGrid.from_schema_dict(static_map)
        assert (5, 5) in grid.obstacles
        assert (5, 6) in grid.obstacles
        assert (5, 7) in grid.obstacles
        assert grid.is_free((0, 0))  # charging station is walkable
        assert grid.is_free((4, 22))  # pickup station is walkable


# ---------------------------------------------------------------------------
# no-path / edge cases
# ---------------------------------------------------------------------------

class TestNoPath:
    def test_fully_reserved_corridor_returns_empty_or_detour(self):
        # 1-wide corridor between two walls; block every cell in it at every
        # early tick so there is truly no way through within a short horizon.
        obstacles = [(3, y) for y in range(0, 30) if y != 15] + [(7, y) for y in range(0, 30) if y != 15]
        grid = WarehouseGrid(obstacles=obstacles, width=30, height=30)
        planner = SpaceTimeAStarPlanner(grid, horizon_padding=5)
        reservation_table = {(5, 15, t): "AMR-BLOCKER" for t in range(0, 50)}
        path = planner.plan_path((0, 15), (10, 15), 0, reservation_table, robot_id="AMR-01")
        # The only corridor cell is permanently blocked -> must be empty,
        # never a path that cuts through the blocked cell.
        blocked_cells = {(5, 15, t) for t in range(0, 50)}
        if path:
            assert not ({(s["x"], s["y"], s["t"]) for s in path} & blocked_cells)
        else:
            assert path == []
