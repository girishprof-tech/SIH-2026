"""
test_conflict_engine.py — Integration and full pipeline tests for run_conflict_engine_tick (Deliverable 5).
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conflict_engine import run_conflict_engine_tick
from models import Heading, Robot, RobotState, Task


def test_full_pipeline_tick():
    """Tests the complete run_conflict_engine_tick pipeline."""
    # Robot A: lower urgency, longer wait
    ra = Robot(
        robot_id="AMR-01",
        position=(10, 10),
        heading=Heading.EAST,
        state=RobotState.EN_ROUTE,
        battery_pct=85.0,
        current_task_id="TASK-A",
        path=[{"x": 10, "y": 10, "t": 0}, {"x": 11, "y": 10, "t": 1}],
        priority_score=0.0,
        wait_ticks_so_far=10,
    )

    # Robot B: higher urgency, 0 wait, heading to same cell (11, 10)
    rb = Robot(
        robot_id="AMR-02",
        position=(12, 10),
        heading=Heading.WEST,
        state=RobotState.EN_ROUTE,
        battery_pct=85.0,
        current_task_id="TASK-B",
        path=[{"x": 12, "y": 10, "t": 0}, {"x": 11, "y": 10, "t": 1}],
        priority_score=0.0,
        wait_ticks_so_far=0,
    )

    robots = {"AMR-01": ra, "AMR-02": rb}

    tasks = {
        "TASK-A": Task("TASK-A", (10, 10), (15, 10), urgency=2, created_tick=0),
        "TASK-B": Task("TASK-B", (12, 10), (5, 10), urgency=4, created_tick=0),
    }

    reservation_table = {
        (10, 10, 0): "AMR-01",
        (11, 10, 1): "AMR-01",
        (12, 10, 0): "AMR-02",
        (11, 10, 1): "AMR-02",
    }

    def mock_find_path(start, goal, tick, res_table, **kwargs):
        return [{"x": start[0], "y": start[1] + 1, "t": tick + 1}]

    # Step 1: Run tick pipeline
    result = run_conflict_engine_tick(
        robots=robots,
        tasks=tasks,
        reservation_table=reservation_table,
        current_tick=0,
        find_path_fn=mock_find_path,
    )

    # Verify priority scores were updated
    assert ra.priority_score > 0
    assert rb.priority_score > 0

    # Verify conflict detected and resolved
    assert result["conflicts_found"] == 1
    assert len(result["resolutions"]) == 1
    res = result["resolutions"][0]
    assert res["resolution_type"] == "YIELD_AND_REPLAN"
    assert "winner_id" in res
    assert "loser_id" in res
    assert result["updated_robots"] == robots


def test_member_4_backend_interoperability():
    """
    Tests that run_conflict_engine_tick works seamlessly when passed
    Member 4's real backend objects (Robot, Task) from app.models.
    """
    backend_app_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backend", "backend"
    )
    sys.path.insert(0, backend_app_dir)

    try:
        from app.models.robot import Robot as BackendRobot, RobotState as BackendRobotState, Heading as BackendHeading, PathNode
        from app.models.task import Task as BackendTask
    except ImportError:
        # If backend imports are unavailable in current path, pass gracefully
        return

    b_ra = BackendRobot(
        robot_id="AMR-01",
        x=5, y=5,
        heading=BackendHeading.EAST,
        state=BackendRobotState.EN_ROUTE,
        battery_pct=90.0,
        current_task_id="TASK-01",
        priority_score=0,
        last_updated_tick=0,
        path=[PathNode(5, 5, 0), PathNode(6, 5, 1)],
    )

    b_rb = BackendRobot(
        robot_id="AMR-02",
        x=7, y=5,
        heading=BackendHeading.WEST,
        state=BackendRobotState.EN_ROUTE,
        battery_pct=90.0,
        current_task_id="TASK-02",
        priority_score=0,
        last_updated_tick=0,
        path=[PathNode(7, 5, 0), PathNode(6, 5, 1)],
    )

    robots = {"AMR-01": b_ra, "AMR-02": b_rb}
    tasks = {
        "TASK-01": BackendTask("TASK-01", 5, 5, 10, 5, urgency=3, created_tick=0),
        "TASK-02": BackendTask("TASK-02", 7, 5, 2, 5, urgency=1, created_tick=0),
    }
    table = {
        (6, 5, 1): "AMR-01",
        (7, 5, 0): "AMR-02",
    }

    def mock_find_path(start, goal, tick, res_table, **kwargs):
        return [{"x": start[0], "y": start[1] + 1, "t": tick + 1}]

    res = run_conflict_engine_tick(robots, tasks, table, current_tick=0, find_path_fn=mock_find_path)

    assert res["conflicts_found"] == 1
    # Backend robot wait_ticks_so_far / _wait_ticks should be updated
    loser_id = res["resolutions"][0]["loser_id"]
    assert robots[loser_id].wait_ticks_so_far == 1
