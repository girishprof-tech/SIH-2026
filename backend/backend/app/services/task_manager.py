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
import json
import logging
import socket
from typing import Any, Callable, Dict, List, Optional

from app.models.robot import AMRType, Robot, RobotState
from app.models.task import Task, TaskStatus
from app.security.hmac_envelope import DEFAULT_SECRET_KEY, sign_payload
from app.services.telemetry_bus import read_latest_telemetry

log = logging.getLogger(__name__)


def get_fleet_peer_ports(orchestrator: Optional[Any] = None) -> Dict[str, int]:
    """Resolves UDP ports for fleet AMRs."""
    if orchestrator and hasattr(orchestrator, "peer_ports") and orchestrator.peer_ports:
        return dict(orchestrator.peer_ports)
    return {f"AMR-{i:02d}": 9000 + i for i in range(1, 11)}


def build_task_assignment_envelope(
    task: Task,
    target_robot_id: str,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for TASK_ASSIGNMENT."""
    payload = {
        "type": "TASK_ASSIGNMENT",
        "sender_id": "DISPATCHER",
        "robot_id": target_robot_id,
        "task": {
            "task_id": task.task_id,
            "pickup": [task.pickup_x, task.pickup_y],
            "dropoff": [task.dropoff_x, task.dropoff_y],
            "urgency": task.urgency,
            "payload_weight_kg": getattr(task, "payload_weight_kg", 0.0),
        },
    }
    return sign_payload(payload, secret_key=secret_key, seq=seq)


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

        eligible_robots = [
            robot for robot in robots.values()
            if robot.state == RobotState.IDLE
            and robot.robot_type in (AMRType.GOODS_TO_PERSON, AMRType.SORTING)
        ]

        for robot in eligible_robots:
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

    def dispatch_to_fleet(
        self,
        task: Task,
        transport_sender: Optional[Any] = None,
        peer_ports: Optional[Dict[str, int]] = None,
        target_robot_id: Optional[str] = None,
        host: str = "127.0.0.1",
        secret_key: str = DEFAULT_SECRET_KEY,
    ) -> Optional[str]:
        """
        Assigns and dispatches a pending task to an available idle robot process via UDP.
        Sources real robot status from read_latest_telemetry().
        """
        best_robot_id: Optional[str] = target_robot_id

        if not best_robot_id:
            telemetry_data = read_latest_telemetry()
            if not telemetry_data or not telemetry_data.get("robots"):
                log.info("DISPATCH_WAIT: No telemetry data available to select idle robot for task %s", task.task_id)
                return None

            # Filter truly IDLE robots
            idle_robots = [
                r for r in telemetry_data["robots"]
                if str(r.get("state", "")).upper() in ("IDLE", "ROBOTSTATE.IDLE")
            ]
            if not idle_robots:
                log.info("DISPATCH_WAIT: No idle robots available for task %s", task.task_id)
                return None

            # Nearest idle assignment
            best_dist = float("inf")
            for r in idle_robots:
                pos = r.get("position")
                if isinstance(pos, dict):
                    rx, ry = int(pos.get("x", 0)), int(pos.get("y", 0))
                elif isinstance(pos, (list, tuple)):
                    rx, ry = int(pos[0]), int(pos[1])
                else:
                    rx, ry = int(r.get("x", 0)), int(r.get("y", 0))
                dist = abs(rx - task.pickup_x) + abs(ry - task.pickup_y)
                if dist < best_dist:
                    best_dist = dist
                    best_robot_id = r.get("id") or r.get("robot_id")

        if not best_robot_id:
            return None

        envelope = build_task_assignment_envelope(task, best_robot_id, secret_key=secret_key)

        try:
            if callable(transport_sender):
                transport_sender(best_robot_id, envelope)
            elif transport_sender is not None and hasattr(transport_sender, "send"):
                transport_sender.send(best_robot_id, envelope)
            else:
                ports = peer_ports or {}
                target_port = ports.get(best_robot_id)
                if not target_port:
                    if "AMR-" in best_robot_id:
                        target_port = 9000 + int(best_robot_id.replace("AMR-", ""))
                    else:
                        target_port = 9001
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    raw = json.dumps(envelope).encode("utf-8")
                    sock.sendto(raw, (host, target_port))
        except Exception as e:
            log.error("DISPATCH_ERROR sending task %s to %s: %s", task.task_id, best_robot_id, e)
            return None

        task.status = TaskStatus.ASSIGNED
        task.assigned_robot_id = best_robot_id
        log.info(
            "TASK_DISPATCHED task_id=%s robot=%s pickup=(%d,%d) dropoff=(%d,%d)",
            task.task_id, best_robot_id, task.pickup_x, task.pickup_y, task.dropoff_x, task.dropoff_y,
        )
        return best_robot_id

