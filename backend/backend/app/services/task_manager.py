"""
TaskManager — manages task lifecycle per SCHEMA.md §5, §17.

Responsibilities:
  - Create tasks from REST injection
  - Assign tasks to available robots (pluggable strategy)
  - Track task status transitions
  - Provide clean interface for TaskAssignmentAdapter (Member 3)

TaskAssignmentAdapter interface is defined here — Member 3 can replace
the default NearestIdleAssignment with a more sophisticated algorithm.
"""

from __future__ import annotations

import abc
import logging
from typing import Dict, List, Optional

from app.models.robot import Robot, RobotState
from app.models.task import Task, TaskStatus

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Task Assignment Interface (plug-in point for Member 3)
# ─────────────────────────────────────────────────────────────────────────────

class AbstractTaskAssigner(abc.ABC):
    """
    Plug-in interface for task assignment algorithms.

    Member 3 can replace NearestIdleAssignment with:
      - Priority-queue based assignment
      - Auction-based multi-robot assignment
      - ML-based assignment
    """

    @abc.abstractmethod
    def assign(
        self,
        task: Task,
        robots: Dict[str, Robot],
        active_tasks: Dict[str, Task],
    ) -> Optional[str]:
        """
        Return the robot_id to assign the task to, or None if no robot available.
        """


class NearestIdleAssignment(AbstractTaskAssigner):
    """
    Simple nearest-idle robot assignment.

    Picks the IDLE robot with minimum Manhattan distance to pickup.
    This is the built-in default.
    """

    def assign(
        self,
        task: Task,
        robots: Dict[str, Robot],
        active_tasks: Dict[str, Task],
    ) -> Optional[str]:
        best_robot_id: Optional[str] = None
        best_dist = float("inf")

        for robot in robots.values():
            if robot.state != RobotState.IDLE:
                continue
            dist = abs(robot.x - task.pickup_x) + abs(robot.y - task.pickup_y)
            if dist < best_dist:
                best_dist = dist
                best_robot_id = robot.robot_id

        return best_robot_id


# ─────────────────────────────────────────────────────────────────────────────
# TaskManager
# ─────────────────────────────────────────────────────────────────────────────

class TaskManager:
    """
    Central task registry and lifecycle manager.

    Does NOT own robots — it queries them via the fleet state reference.
    """

    def __init__(self, assigner: Optional[AbstractTaskAssigner] = None) -> None:
        self._tasks: Dict[str, Task] = {}
        self._assigner: AbstractTaskAssigner = assigner or NearestIdleAssignment()

    # ── Task CRUD ─────────────────────────────────────────────────────────────

    def create_task(
        self,
        pickup_x: int, pickup_y: int,
        dropoff_x: int, dropoff_y: int,
        urgency: int,
        current_tick: int,
    ) -> Task:
        task = Task(
            task_id=Task.generate_id(),
            pickup_x=pickup_x,
            pickup_y=pickup_y,
            dropoff_x=dropoff_x,
            dropoff_y=dropoff_y,
            urgency=urgency,
            created_tick=current_tick,
            status=TaskStatus.PENDING,
        )
        self._tasks[task.task_id] = task
        log.info("TASK_CREATED task_id=%s urgency=%d", task.task_id, urgency)
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def all_tasks(self) -> Dict[str, Task]:
        return self._tasks

    def pending_tasks(self) -> List[Task]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.PENDING]

    # ── Assignment ────────────────────────────────────────────────────────────

    def try_assign(
        self,
        task: Task,
        robots: Dict[str, Robot],
        current_tick: int,
    ) -> Optional[str]:
        """
        Attempt to assign a pending task to an available robot.

        Returns robot_id if assigned, None otherwise.
        """
        if task.status != TaskStatus.PENDING:
            return None

        robot_id = self._assigner.assign(task, robots, self._tasks)
        if robot_id is None:
            return None

        task.status = TaskStatus.ASSIGNED
        task.assigned_robot_id = robot_id
        task._assigned_tick = current_tick

        robot = robots[robot_id]
        robot.current_task_id = task.task_id
        robot.state = RobotState.EN_ROUTE

        log.info(
            "TASK_ASSIGNED task_id=%s robot=%s tick=%d",
            task.task_id, robot_id, current_tick,
        )
        return robot_id

    def mark_in_progress(self, task_id: str, current_tick: int) -> None:
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.ASSIGNED:
            task.status = TaskStatus.IN_PROGRESS
            log.info("TASK_IN_PROGRESS task_id=%s tick=%d", task_id, current_tick)

    def mark_completed(self, task_id: str, robot: Robot, current_tick: int) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = TaskStatus.COMPLETED
        task._completed_tick = current_tick
        robot.current_task_id = None
        robot.state = RobotState.IDLE
        robot._wait_ticks = 0
        log.info(
            "TASK_COMPLETED task_id=%s robot=%s tick=%d",
            task_id, robot.robot_id, current_tick,
        )

    # ── Tick processing ───────────────────────────────────────────────────────

    def process_pending(
        self,
        robots: Dict[str, Robot],
        current_tick: int,
    ) -> None:
        """Assign any unassigned pending tasks. Called every tick."""
        for task in self.pending_tasks():
            self.try_assign(task, robots, current_tick)

    def replace_assigner(self, assigner: AbstractTaskAssigner) -> None:
        """Hot-swap the assignment algorithm without restarting the server."""
        self._assigner = assigner
        log.info("TaskManager: assigner replaced with %s", type(assigner).__name__)
