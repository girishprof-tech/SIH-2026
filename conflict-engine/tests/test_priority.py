"""
test_priority.py — Unit tests for calculate_priority_score (Deliverable 2).
Owner: Member 3 — Conflict Resolution & Fleet Coordination.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Heading, Robot, RobotState, Task
from priority import calculate_priority_score


def make_test_robot(
    robot_id: str = "AMR-01",
    battery_pct: float = 80.0,
    wait_ticks_so_far: int = 0,
    current_task_id: str = "TASK-01",
) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=(5, 5),
        heading=Heading.NORTH,
        state=RobotState.EN_ROUTE,
        battery_pct=battery_pct,
        current_task_id=current_task_id,
        wait_ticks_so_far=wait_ticks_so_far,
        priority_score=0.0,
    )


def make_test_task(task_id: str = "TASK-01", urgency: int = 3) -> Task:
    return Task(
        task_id=task_id,
        pickup=(2, 2),
        dropoff=(10, 10),
        urgency=urgency,
        created_tick=0,
    )


def test_high_urgency_beats_low_urgency():
    """1. A high-urgency task beats a low-urgency one, all else equal."""
    robot = make_test_robot()
    task_low = make_test_task(urgency=1)
    task_high = make_test_task(urgency=5)
    dist = 10

    score_low = calculate_priority_score(robot, task_low, dist)
    score_high = calculate_priority_score(robot, task_high, dist)

    assert score_high > score_low
    assert score_high - score_low == 400.0  # (5 - 1) * 100


def test_low_battery_bonus():
    """2. A low-battery robot (< 20%) gets the +500 bonus."""
    robot_normal = make_test_robot(battery_pct=50.0)
    robot_low = make_test_robot(battery_pct=18.5)
    task = make_test_task(urgency=2)
    dist = 5

    score_normal = calculate_priority_score(robot_normal, task, dist)
    score_low = calculate_priority_score(robot_low, task, dist)

    assert score_low > score_normal
    assert score_low - score_normal == 500.0


def test_starvation_prevention():
    """
    3. Demonstrates starvation prevention numerically:
    A robot with low urgency (urgency=1) but high wait_ticks_so_far eventually
    exceeds a robot with high urgency (urgency=4) but zero wait time.
    """
    robot_waiting = make_test_robot(robot_id="AMR-01", battery_pct=75.0, wait_ticks_so_far=0)
    task_low = make_test_task(urgency=1)

    robot_fresh = make_test_robot(robot_id="AMR-02", battery_pct=75.0, wait_ticks_so_far=0)
    task_high = make_test_task(urgency=4)

    dist = 10

    # Initially fresh robot with urgency=4 wins: 400 - 10 = 390 vs 100 - 10 = 90
    initial_waiting_score = calculate_priority_score(robot_waiting, task_low, dist)
    initial_fresh_score = calculate_priority_score(robot_fresh, task_high, dist)
    assert initial_fresh_score > initial_waiting_score

    # Simulate waiting: after 31 ticks (31 * 10 = 310 points), score becomes 90 + 310 = 400 > 390
    robot_waiting.wait_ticks_so_far = 31
    accumulated_waiting_score = calculate_priority_score(robot_waiting, task_low, dist)

    assert accumulated_waiting_score > initial_fresh_score
    assert accumulated_waiting_score == 400.0
    assert initial_fresh_score == 390.0


def test_distance_reduces_score():
    """4. Distance-to-goal reduces the score correctly (-1 point per tile)."""
    robot = make_test_robot()
    task = make_test_task(urgency=3)

    score_near = calculate_priority_score(robot, task, distance_to_goal=5)
    score_far = calculate_priority_score(robot, task, distance_to_goal=20)

    assert score_near > score_far
    assert score_near - score_far == 15.0  # (20 - 5) * 1


def test_exact_tie_score():
    """5. Verifies exact numerical tie calculation for tie-breaker verification."""
    robot_a = make_test_robot(robot_id="AMR-01", battery_pct=80.0, wait_ticks_so_far=2)
    robot_b = make_test_robot(robot_id="AMR-02", battery_pct=80.0, wait_ticks_so_far=2)
    task = make_test_task(urgency=3)
    dist = 10

    score_a = calculate_priority_score(robot_a, task, dist)
    score_b = calculate_priority_score(robot_b, task, dist)

    assert score_a == score_b
    assert score_a == 300.0 + 0.0 + 20.0 - 10.0  # 310.0
