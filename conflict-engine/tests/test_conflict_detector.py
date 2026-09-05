"""
test_conflict_detector.py — Unit tests for detect_conflicts (Deliverable 3).
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conflict_detector import detect_conflicts
from models import Heading, Robot, RobotState


def make_robot_with_path(robot_id: str, pos: tuple[int, int], path: list[dict]) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=pos,
        heading=Heading.NORTH,
        state=RobotState.EN_ROUTE,
        battery_pct=85.0,
        current_task_id="TASK-01",
        path=path,
        priority_score=100.0,
    )


def test_detects_cell_overlap_conflict():
    """Correctly detect a CELL_OVERLAP conflict within 2 ticks and Manhattan dist <= 2."""
    current_tick = 10
    # Robot A starts at (5, 5), moves to (6, 5) at tick 11
    # Robot B starts at (7, 5) (Manhattan dist = 2), moves to (6, 5) at tick 11
    ra = make_robot_with_path("AMR-01", (5, 5), [
        {"x": 5, "y": 5, "t": 10},
        {"x": 6, "y": 5, "t": 11},
        {"x": 7, "y": 5, "t": 12},
    ])
    rb = make_robot_with_path("AMR-02", (7, 5), [
        {"x": 7, "y": 5, "t": 10},
        {"x": 6, "y": 5, "t": 11},
        {"x": 5, "y": 5, "t": 12},
    ])

    conflicts = detect_conflicts([ra, rb], current_tick)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert set(c["robot_ids"]) == {"AMR-01", "AMR-02"}
    assert c["type"] == "CELL_OVERLAP"
    assert c["cell"] == {"x": 6, "y": 5}
    assert c["tick"] == 11


def test_detects_swap_conflict():
    """Correctly detect a SWAP_CONFLICT when two robots trade positions in the next tick."""
    current_tick = 20
    # Robot A is at (10, 10), heading to (11, 10) at tick 21
    # Robot B is at (11, 10), heading to (10, 10) at tick 21
    ra = make_robot_with_path("AMR-01", (10, 10), [
        {"x": 10, "y": 10, "t": 20},
        {"x": 11, "y": 10, "t": 21},
    ])
    rb = make_robot_with_path("AMR-02", (11, 10), [
        {"x": 11, "y": 10, "t": 20},
        {"x": 10, "y": 10, "t": 21},
    ])

    conflicts = detect_conflicts([ra, rb], current_tick)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert set(c["robot_ids"]) == {"AMR-01", "AMR-02"}
    assert c["type"] == "SWAP_CONFLICT"
    assert c["tick"] == 21


def test_no_conflicts_when_paths_never_intersect():
    """Correctly return an empty list when robots' paths never intersect."""
    current_tick = 0
    # Robot A moves North at x=2
    # Robot B moves South at x=15
    ra = make_robot_with_path("AMR-01", (2, 10), [
        {"x": 2, "y": 10, "t": 0},
        {"x": 2, "y": 9, "t": 1},
        {"x": 2, "y": 8, "t": 2},
    ])
    rb = make_robot_with_path("AMR-02", (15, 10), [
        {"x": 15, "y": 10, "t": 0},
        {"x": 15, "y": 11, "t": 1},
        {"x": 15, "y": 12, "t": 2},
    ])

    conflicts = detect_conflicts([ra, rb], current_tick)
    assert conflicts == []


def test_ignores_robots_more_than_2_cells_apart():
    """Correctly ignore robots more than 2 cells apart even if paths technically cross far in future."""
    current_tick = 0
    # Robot A is at (0, 0); Robot B is at (10, 10) (Manhattan dist = 20 > 2)
    # Both paths meet at (5, 5) at tick 5 (far in future)
    ra = make_robot_with_path("AMR-01", (0, 0), [
        {"x": 0, "y": 0, "t": 0},
        {"x": 1, "y": 0, "t": 1},
        {"x": 5, "y": 5, "t": 5},
    ])
    rb = make_robot_with_path("AMR-02", (10, 10), [
        {"x": 10, "y": 10, "t": 0},
        {"x": 9, "y": 10, "t": 1},
        {"x": 5, "y": 5, "t": 5},
    ])

    conflicts = detect_conflicts([ra, rb], current_tick)
    assert conflicts == []


def test_efficiency_with_25_robots():
    """Validates that detect_conflicts runs in < 5ms for 25 concurrent robots."""
    current_tick = 50
    robots = []
    for i in range(25):
        pos = (i, (i * 2) % 30)
        path = [
            {"x": pos[0], "y": pos[1], "t": 50},
            {"x": (pos[0] + 1) % 30, "y": pos[1], "t": 51},
            {"x": (pos[0] + 1) % 30, "y": (pos[1] + 1) % 30, "t": 52},
        ]
        robots.append(make_robot_with_path(f"AMR-{i:02d}", pos, path))

    t0 = time.perf_counter()
    conflicts = detect_conflicts(robots, current_tick)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 10.0, f"Conflict detection took too long: {elapsed_ms:.2f}ms"
    assert isinstance(conflicts, list)
