"""
test_mission_lifecycle.py — Unit tests for Mission & Task Lifecycle on Robot Node.
Verifies full assignment-to-completion cycle and idempotent task delivery.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.robot_fsm import RobotEvent, RobotFSM, RobotState
from models import Task


class MissionTracker:
    """Simulates the robot node's task management and idempotency guard."""

    def __init__(self, robot_id: str = "AMR-01") -> None:
        self.robot_id = robot_id
        self.fsm = RobotFSM(RobotState.IDLE)
        self.active_task: Task | None = None
        self.completed_tasks: set[str] = set()

    def handle_task_assignment(self, task_dict: dict) -> bool:
        """
        Idempotently handles a TASK_ASSIGNMENT message.
        Returns True if newly accepted, False if duplicate or rejected.
        """
        tid = task_dict["task_id"]
        # Idempotency guard: ignore if already completed or already active
        if tid in self.completed_tasks:
            return False
        if self.active_task and self.active_task.task_id == tid:
            return False

        if self.fsm.state != RobotState.IDLE:
            return False

        self.active_task = Task(
            task_id=tid,
            pickup=tuple(task_dict["pickup"]),
            dropoff=tuple(task_dict["dropoff"]),
            urgency=task_dict.get("urgency", 3),
            created_tick=task_dict.get("created_tick", 0),
            assigned_robot_id=self.robot_id,
            status="ASSIGNED",
        )
        self.fsm.transition(RobotEvent.TASK_RECEIVED)
        return True

    def mark_pickup_complete(self) -> None:
        if self.active_task:
            self.active_task.status = "IN_PROGRESS"
            self.fsm.transition(RobotEvent.PICKUP_COMPLETE)

    def mark_dropoff_complete(self) -> None:
        if self.active_task:
            self.active_task.status = "COMPLETED"
            self.completed_tasks.add(self.active_task.task_id)
            self.active_task = None
            self.fsm.transition(RobotEvent.MISSION_COMPLETE)


def test_full_assignment_lifecycle():
    """Verifies complete task assignment from IDLE to COMPLETED and back to IDLE."""
    tracker = MissionTracker("AMR-01")
    assert tracker.fsm.state == RobotState.IDLE

    # Receive task
    task_spec = {"task_id": "TASK-101", "pickup": (2, 5), "dropoff": (15, 5), "urgency": 4}
    accepted = tracker.handle_task_assignment(task_spec)
    assert accepted is True
    assert tracker.fsm.state == RobotState.ASSIGNED
    assert tracker.active_task.status == "ASSIGNED"

    # Plan path -> EN_ROUTE_PICKUP
    tracker.fsm.transition(RobotEvent.PATH_PLANNED)
    assert tracker.fsm.state == RobotState.EN_ROUTE_PICKUP

    # Arrive at pickup -> PICKING (1-tick atomic)
    tracker.fsm.transition(RobotEvent.PICKUP_REACHED)
    assert tracker.fsm.state == RobotState.PICKING

    # Pickup complete -> EN_ROUTE_DROPOFF
    tracker.mark_pickup_complete()
    assert tracker.fsm.state == RobotState.EN_ROUTE_DROPOFF
    assert tracker.active_task.status == "IN_PROGRESS"

    # Arrive at dropoff -> DROPPING (1-tick atomic)
    tracker.fsm.transition(RobotEvent.DROPOFF_REACHED)
    assert tracker.fsm.state == RobotState.DROPPING

    # Dropoff complete -> IDLE
    tracker.mark_dropoff_complete()
    assert tracker.fsm.state == RobotState.IDLE
    assert tracker.active_task is None
    assert "TASK-101" in tracker.completed_tasks


def test_idempotent_duplicate_task_delivery():
    """Verifies that duplicate TASK_ASSIGNMENT messages are rejected and ignored."""
    tracker = MissionTracker("AMR-01")
    task_spec = {"task_id": "TASK-101", "pickup": (2, 5), "dropoff": (15, 5)}

    # 1. First delivery accepted
    assert tracker.handle_task_assignment(task_spec) is True

    # 2. Duplicate while active -> rejected
    assert tracker.handle_task_assignment(task_spec) is False
    assert tracker.fsm.state == RobotState.ASSIGNED

    # Complete the task
    tracker.fsm.transition(RobotEvent.PATH_PLANNED)
    tracker.fsm.transition(RobotEvent.PICKUP_REACHED)
    tracker.mark_pickup_complete()
    tracker.fsm.transition(RobotEvent.DROPOFF_REACHED)
    tracker.mark_dropoff_complete()
    assert tracker.fsm.state == RobotState.IDLE

    # 3. Duplicate after completion -> rejected
    assert tracker.handle_task_assignment(task_spec) is False
    assert tracker.fsm.state == RobotState.IDLE
