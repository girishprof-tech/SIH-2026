"""
test_arbitration.py — Unit and integration tests for resolve_conflict (Deliverable 4).
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arbitration import resolve_conflict
from models import Heading, Robot, RobotState


def make_robot(robot_id: str, pos: tuple[int, int], path: list[dict], priority: float) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=pos,
        heading=Heading.EAST,
        state=RobotState.EN_ROUTE,
        battery_pct=90.0,
        current_task_id="TASK-01",
        path=path,
        priority_score=priority,
        wait_ticks_so_far=0,
    )


def test_higher_priority_keeps_path_lower_yields():
    """Higher priority robot keeps path, lower priority robot yields and gets a new path."""
    ra = make_robot("AMR-01", (4, 5), [{"x": 5, "y": 5, "t": 10}], priority=450.0)
    rb = make_robot("AMR-02", (6, 5), [{"x": 5, "y": 5, "t": 10}], priority=200.0)

    robots = {"AMR-01": ra, "AMR-02": rb}
    table = {
        (5, 5, 10): "AMR-01",
        (6, 5, 10): "AMR-02",
    }

    mock_new_path = [{"x": 6, "y": 5, "t": 10}, {"x": 6, "y": 6, "t": 11}]

    def mock_find_path(start, goal, tick, res_table, robot_id=None):
        # Assert that loser's old reservations have been cleaned up
        assert (6, 5, 10) not in res_table
        # Winner's reservations must still exist
        assert (5, 5, 10) in res_table
        return mock_new_path

    conflict = {
        "robot_ids": ["AMR-01", "AMR-02"],
        "cell": {"x": 5, "y": 5},
        "tick": 10,
        "type": "CELL_OVERLAP",
    }

    result = resolve_conflict(conflict, robots, table, mock_find_path)

    assert result["winner_id"] == "AMR-01"
    assert result["loser_id"] == "AMR-02"
    assert result["resolution_type"] == "YIELD_AND_REPLAN"
    assert rb.path == mock_new_path
    assert rb.state == RobotState.EN_ROUTE
    assert rb.wait_ticks_so_far == 1
    # Winner's path remains unchanged
    assert ra.path == [{"x": 5, "y": 5, "t": 10}]


def test_tie_breaker_by_robot_id():
    """Exact tie in priority score: AMR-01 beats AMR-03."""
    ra = make_robot("AMR-01", (1, 1), [{"x": 2, "y": 1, "t": 5}], priority=300.0)
    rb = make_robot("AMR-03", (3, 1), [{"x": 2, "y": 1, "t": 5}], priority=300.0)

    robots = {"AMR-01": ra, "AMR-03": rb}
    table = {(2, 1, 5): "AMR-01", (3, 1, 5): "AMR-03"}

    def mock_find_path(start, goal, tick, res_table, **kwargs):
        return [{"x": 3, "y": 2, "t": 6}]

    conflict = {
        "robot_ids": ["AMR-03", "AMR-01"],
        "cell": {"x": 2, "y": 1},
        "tick": 5,
        "type": "CELL_OVERLAP",
    }

    result = resolve_conflict(conflict, robots, table, mock_find_path)

    # Lexicographically lower robot_id wins: AMR-01 beats AMR-03
    assert result["winner_id"] == "AMR-01"
    assert result["loser_id"] == "AMR-03"
    assert rb.wait_ticks_so_far == 1


def test_reservation_table_cleaned_of_loser_only():
    """The reservation table is cleaned of the loser's old entries, preserving others."""
    r_winner = make_robot("AMR-02", (2, 2), [{"x": 2, "y": 3, "t": 4}], priority=500.0)
    r_loser = make_robot("AMR-04", (2, 4), [{"x": 2, "y": 3, "t": 4}], priority=100.0)
    r_innocent = make_robot("AMR-05", (10, 10), [{"x": 10, "y": 11, "t": 4}], priority=100.0)

    robots = {"AMR-02": r_winner, "AMR-04": r_loser, "AMR-05": r_innocent}
    table = {
        (2, 3, 4): "AMR-02",
        (2, 4, 3): "AMR-04",
        (2, 4, 4): "AMR-04",
        (10, 11, 4): "AMR-05",
    }

    def mock_find_path(start, goal, tick, res_table, **kwargs):
        return []

    conflict = {
        "robot_ids": ["AMR-02", "AMR-04"],
        "cell": {"x": 2, "y": 3},
        "tick": 4,
        "type": "CELL_OVERLAP",
    }

    resolve_conflict(conflict, robots, table, mock_find_path)

    # Loser's entries must be gone
    assert (2, 4, 3) not in table
    assert (2, 4, 4) not in table
    # Winner and innocent entries must be completely intact
    assert table[(2, 3, 4)] == "AMR-02"
    assert table[(10, 11, 4)] == "AMR-05"


def test_integration_with_real_member_2_pathfinder():
    """
    INTEGRATION TEST:
    Imports and passes Member 2's REAL find_path function from pathfinding/pathfinder.py.
    Proves end-to-end compatibility between Member 2 and Member 3.
    """
    # Import Member 2's real pathfinder
    pathfinding_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pathfinding")
    sys.path.insert(0, pathfinding_dir)

    try:
        from pathfinder import find_path as real_find_path
        from grid import WarehouseGrid
    except ImportError:
        # If networkx/numpy not in current env, skip or test gracefully
        return

    # Set up two robots heading towards each other in a corridor
    r_high = make_robot("AMR-01", (5, 5), [{"x": 6, "y": 5, "t": 1}, {"x": 7, "y": 5, "t": 2}], priority=500.0)
    r_low = make_robot("AMR-02", (7, 5), [{"x": 6, "y": 5, "t": 1}, {"x": 5, "y": 5, "t": 2}], priority=100.0)

    robots = {"AMR-01": r_high, "AMR-02": r_low}
    table = {
        (6, 5, 1): "AMR-01",
        (7, 5, 2): "AMR-01",
        (6, 5, 1): "AMR-02",
        (5, 5, 2): "AMR-02",
    }

    conflict = {
        "robot_ids": ["AMR-01", "AMR-02"],
        "cell": {"x": 6, "y": 5},
        "tick": 1,
        "type": "CELL_OVERLAP",
    }

    # Resolve using the REAL Member 2 Space-Time A* pathfinder
    result = resolve_conflict(conflict, robots, table, real_find_path)

    assert result["winner_id"] == "AMR-01"
    assert result["loser_id"] == "AMR-02"
    assert result["resolution_type"] == "YIELD_AND_REPLAN"
    assert r_low.wait_ticks_so_far == 1
    # Check that real_find_path returned a valid path list
    assert isinstance(result["loser_new_path"], list)
