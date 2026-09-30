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
from app.models.task import Task, TaskStatus, TaskType
from app.security.hmac_envelope import DEFAULT_SECRET_KEY, sign_payload
from app.services.telemetry_bus import read_latest_telemetry

log = logging.getLogger(__name__)


def get_fleet_peer_ports(orchestrator: Optional[Any] = None) -> Dict[str, int]:
    """Resolves UDP ports for fleet AMRs."""
    if orchestrator and hasattr(orchestrator, "peer_ports") and orchestrator.peer_ports:
        return dict(orchestrator.peer_ports)
    ports = {
        "G2P-01": 9001, "G2P-02": 9002, "G2P-03": 9003, "G2P-04": 9004, "G2P-05": 9005,
        "SORT-01": 9006, "SORT-02": 9007, "SORT-03": 9008,
        "AUDIT-01": 9009, "AUDIT-02": 9010,
    }
    for i in range(1, 11):
        ports[f"AMR-{i:02d}"] = 9000 + i
    return ports


def build_task_assignment_envelope(
    task: Task,
    target_robot_id: str,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
    station_role: Optional[str] = None,
    station_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for TASK_ASSIGNMENT."""
    task_type_val = task.task_type.value if hasattr(task.task_type, "value") else str(getattr(task, "task_type", "STANDARD"))
    payload = {
        "type": "TASK_ASSIGNMENT",
        "sender_id": station_id or "DISPATCHER",
        "robot_id": target_robot_id,
        "task": {
            "task_id": task.task_id,
            "pickup": [task.pickup_x, task.pickup_y],
            "dropoff": [task.dropoff_x, task.dropoff_y],
            "urgency": task.urgency,
            "payload_weight_kg": getattr(task, "payload_weight_kg", 0.0),
            "task_type": task_type_val,
            "target_shelf_id": getattr(task, "target_shelf_id", None),
            "sku_to_pick": getattr(task, "sku_to_pick", None),
            "quantity": getattr(task, "quantity", 1),
        },
    }
    if station_role:
        payload["station_role"] = station_role
        payload["station_id"] = station_id or station_role
    return sign_payload(payload, secret_key=secret_key, seq=seq)


def build_task_announcement_envelope(
    task: Task,
    secret_key: str = DEFAULT_SECRET_KEY,
    seq: Optional[int] = None,
    station_role: Optional[str] = None,
    station_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Constructs a signed cryptographic HMAC envelope for TASK_ANNOUNCEMENT."""
    task_type_val = task.task_type.value if hasattr(task.task_type, "value") else str(getattr(task, "task_type", "STANDARD"))
    payload = {
        "type": "TASK_ANNOUNCEMENT",
        "sender_id": station_id or "DISPATCHER",
        "task": {
            "task_id": task.task_id,
            "pickup": [task.pickup_x, task.pickup_y],
            "dropoff": [task.dropoff_x, task.dropoff_y],
            "urgency": task.urgency,
            "payload_weight_kg": getattr(task, "payload_weight_kg", 0.0),
            "task_type": task_type_val,
            "target_shelf_id": getattr(task, "target_shelf_id", None),
            "sku_to_pick": getattr(task, "sku_to_pick", None),
            "quantity": getattr(task, "quantity", 1),
            "return_to_home": getattr(task, "return_to_home", True),
            "home_slot": getattr(task, "home_slot", None) or [task.pickup_x, task.pickup_y],
            "route_code": getattr(task, "route_code", None),
            "order_id": getattr(task, "order_id", None),
            "destination_gate": getattr(task, "destination_gate", None),
        },
    }
    if station_role:
        payload["station_role"] = station_role
        payload["station_id"] = station_id or station_role
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
    Simple nearest-idle robot assignment with pod-slot locking awareness (Phase 1.5 Fix 1).

    Picks the IDLE robot with minimum Manhattan distance to pickup.
    Refuses assignment if another task or robot has claimed the targeted shelf_id.
    """

    def assign(
        self,
        task: Task,
        robots: Dict[str, Robot],
        active_tasks: Optional[Dict[str, Task] | List[Task]] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        best_robot_id: Optional[str] = None
        best_dist = float("inf")

        t_type = getattr(task, "task_type", TaskType.STANDARD)
        shelf_id = getattr(task, "target_shelf_id", None)

        task_dict: Dict[str, Task] = {}
        if active_tasks:
            if isinstance(active_tasks, dict):
                task_dict = active_tasks
            else:
                task_dict = {t.task_id: t for t in active_tasks}

        # Phase 1.5 Fix 1: Refuse assignment for duplicate/in-flight shelf tasks
        if shelf_id and t_type in (TaskType.RETRIEVE_POD, TaskType.RETURN_POD, TaskType.PICK_ITEM):
            # 1. Check in-flight active tasks
            for other_id, other_task in task_dict.items():
                if other_id != task.task_id and other_task.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                    if getattr(other_task, "target_shelf_id", None) == shelf_id:
                        log.warning(
                            "NearestIdleAssignment: Refusing assignment for task %s; shelf %s already targeted by %s",
                            task.task_id, shelf_id, other_id,
                        )
                        return None

            # 2. Check if any robot is already carrying this pod
            for robot in robots.values():
                if getattr(robot, "carrying_pod_id", None) == shelf_id:
                    log.warning(
                        "NearestIdleAssignment: Refusing assignment for task %s; shelf %s already carried by %s",
                        task.task_id, shelf_id, robot.robot_id,
                    )
                    return None

            # 3. Check active pod claims in reservations
            from app.services.reservations import get_pod_claim
            claimant = get_pod_claim(shelf_id)
            if claimant is not None:
                log.warning(
                    "NearestIdleAssignment: Refusing assignment for task %s; shelf %s has active pod claim by %s",
                    task.task_id, shelf_id, claimant,
                )
                return None
        
        # Explicit decoupled robot type filtering
        if t_type in (TaskType.INDUCT_BATCH, TaskType.DECANT_TO_CHUTE, TaskType.CONSOLIDATE_EXPORT):
            eligible_robots = [
                robot for robot in robots.values()
                if robot.state == RobotState.IDLE and robot.robot_type == AMRType.SORTING
            ]
        elif t_type in (TaskType.RETRIEVE_POD, TaskType.RETURN_POD, TaskType.PICK_ITEM):
            eligible_robots = [
                robot for robot in robots.values()
                if robot.state == RobotState.IDLE and robot.robot_type == AMRType.GOODS_TO_PERSON
            ]
        else:
            # Standard tasks: match capable carrying robot
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
        task_type: TaskType = TaskType.STANDARD,
        target_shelf_id: Optional[str] = None,
        sku_to_pick: Optional[str] = None,
        quantity: int = 1,
        payload_weight_kg: float = 0.0,
        return_to_home: bool = True,
        home_slot: Optional[Tuple[int, int]] = None,
        destination_zone: Optional[str] = None,
        pick_station_id: Optional[str] = None,
        route_code: Optional[str] = None,
        task_id: Optional[str] = None,
        order_id: Optional[str] = None,
        destination_gate: Optional[str] = None,
    ) -> Task:
        # Part D: If payload_weight_kg is 0.0 and task targets a shelf, compute real weight from ledger
        if payload_weight_kg == 0.0 and target_shelf_id and task_type in (TaskType.RETRIEVE_POD, TaskType.RETURN_POD):
            try:
                from app.services.inventory_ledger import InventoryLedger
                ledger = InventoryLedger()
                payload_weight_kg = ledger.get_shelf_weight_kg(target_shelf_id)
            except Exception:
                pass

        task = Task(
            task_id=task_id or Task.generate_id(),
            pickup_x=pickup_x,
            pickup_y=pickup_y,
            dropoff_x=dropoff_x,
            dropoff_y=dropoff_y,
            urgency=urgency,
            created_tick=current_tick,
            status=TaskStatus.PENDING,
            task_type=task_type,
            target_shelf_id=target_shelf_id,
            sku_to_pick=sku_to_pick,
            quantity=quantity,
            payload_weight_kg=payload_weight_kg,
            return_to_home=return_to_home,
            home_slot=home_slot or (pickup_x, pickup_y),
            destination_zone=destination_zone,
            pick_station_id=pick_station_id,
            route_code=route_code,
            order_id=order_id,
            destination_gate=destination_gate,
        )
        self._tasks[task.task_id] = task
        log.info(
            "TASK_CREATED task_id=%s urgency=%d type=%s target_shelf=%s sku=%s return_home=%s",
            task.task_id, urgency, task_type.value if hasattr(task_type, "value") else task_type,
            target_shelf_id, sku_to_pick, return_to_home,
        )

        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def all_tasks(self) -> Dict[str, Task]:
        return self._tasks

    def pending_tasks(self) -> List[Task]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.PENDING]

    def clear(self) -> None:
        """Clear all tasks from the registry on simulation reset."""
        self._tasks.clear()
        log.info("TASK_MANAGER_CLEARED: All tasks removed.")

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
        try:
            from app.services.job_journal import JobJournal
            JobJournal().log_completion(job_id=task_id, robot_id=robot.robot_id, tick=current_tick)
        except Exception:
            pass

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

    def _send_envelope_to_robot(
        self,
        robot_id: str,
        envelope: Dict[str, Any],
        transport_sender: Optional[Any],
        peer_ports: Dict[str, int],
        host: str,
    ) -> None:
        if callable(transport_sender):
            transport_sender(robot_id, envelope)
        elif transport_sender is not None and hasattr(transport_sender, "send"):
            transport_sender.send(robot_id, envelope)
        else:
            target_port = peer_ports.get(robot_id)
            if not target_port:
                if "AMR-" in robot_id:
                    target_port = 9000 + int(robot_id.replace("AMR-", ""))
                else:
                    target_port = 9001
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                raw = json.dumps(envelope).encode("utf-8")
                sock.sendto(raw, (host, target_port))

    def record_claim(
        self,
        task_id: str,
        winner_id: str,
        lease_ticks: int = 40,
        current_tick: int = 0,
    ) -> None:
        """Records winning contract-net claim and establishes worker lease."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.ASSIGNED
            task.assigned_robot_id = winner_id
            task._assigned_tick = current_tick
            task.lease_expires_tick = current_tick + lease_ticks
            task.unclaimed_reason = None
            log.info(
                "TASK_CLAIM_RECORDED task_id=%s winner=%s lease_expires=%d tick=%d",
                task_id, winner_id, task.lease_expires_tick, current_tick,
            )

    def record_lease_heartbeat(
        self,
        task_id: str,
        robot_id: str,
        lease_ticks: int = 40,
        current_tick: int = 0,
    ) -> None:
        """Renews active worker lease for task."""
        task = self._tasks.get(task_id)
        if task and (task.assigned_robot_id == robot_id or task.assigned_robot_id is None):
            task.assigned_robot_id = robot_id
            task.status = TaskStatus.IN_PROGRESS
            task.lease_expires_tick = current_tick + lease_ticks

    def mark_completed_by_id(
        self,
        task_id: str,
        robot_id: Optional[str] = None,
        current_tick: int = 0,
    ) -> None:
        """Marks task completed from decentralized notification."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task._completed_tick = current_tick
            task.lease_expires_tick = None
            if robot_id:
                task.assigned_robot_id = robot_id
            log.info("TASK_COMPLETED task_id=%s robot=%s tick=%d", task_id, robot_id, current_tick)
            try:
                from app.services.job_journal import JobJournal
                JobJournal().log_completion(job_id=task_id, robot_id=robot_id, tick=current_tick)
            except Exception:
                pass

    def cancel_task(self, task_id: str, reason: str = "operator_cancel") -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.CANCELLED
        task.lease_expires_tick = None
        log.info("TASK_CANCELLED task_id=%s reason=%s", task_id, reason)
        try:
            from app.services.job_journal import JobJournal
            JobJournal().log_cancellation(job_id=task_id, reason=reason)
        except Exception:
            pass
        return True

    def mark_failed(self, task_id: str, reason: str = "execution_failed") -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.FAILED
        task.lease_expires_tick = None
        log.info("TASK_FAILED task_id=%s reason=%s", task_id, reason)
        try:
            from app.services.job_journal import JobJournal
            JobJournal().log_failure(job_id=task_id, reason=reason)
        except Exception:
            pass
        return True

    def check_leases_and_unclaimed(
        self,
        current_tick: int,
        peer_ports: Optional[Dict[str, int]] = None,
    ) -> None:
        """
        Evaluates task leases and bidding windows.
        If a lease expires (worker died or partitioned), re-announces the task.
        If an announced task receives no bids within N windows, marks UNCLAIMED and periodically re-announces.
        """
        for task in list(self._tasks.values()):
            # 1. Lease expiry on assigned/in-progress tasks
            if task.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                if task.lease_expires_tick is not None and current_tick > task.lease_expires_tick:
                    log.warning(
                        "TASK_LEASE_EXPIRED task_id=%s holder=%s expired_at=%d current=%d. Re-announcing.",
                        task.task_id, task.assigned_robot_id, task.lease_expires_tick, current_tick,
                    )
                    dead_bot = task.assigned_robot_id
                    task.assigned_robot_id = None
                    task.status = TaskStatus.UNCLAIMED
                    task.unclaimed_reason = "Worker lease expired (unresponsive or partitioned)"
                    task.lease_expires_tick = None
                    if task.target_shelf_id and dead_bot:
                        try:
                            from app.services.reservations import release_pod
                            release_pod(task.target_shelf_id, dead_bot)
                        except Exception:
                            pass
                    self.dispatch_to_fleet(task, peer_ports=peer_ports, current_tick=current_tick)

            # 2. Unclaimed tasks re-announcement
            elif task.status in (TaskStatus.PENDING, TaskStatus.ANNOUNCED, TaskStatus.BIDDING, TaskStatus.UNCLAIMED):
                last_ann = task.last_announced_tick or 0
                elapsed = current_tick - last_ann
                if elapsed >= 4 and task.status != TaskStatus.UNCLAIMED:
                    task.status = TaskStatus.UNCLAIMED
                    task.unclaimed_reason = "No eligible robots or insufficient battery for trip"

                # Periodically re-announce unclaimed tasks (user choice A3)
                if elapsed >= 10:
                    self.dispatch_to_fleet(task, peer_ports=peer_ports, current_tick=current_tick)

    def dispatch_to_fleet(
        self,
        task: Task,
        transport_sender: Optional[Any] = None,
        peer_ports: Optional[Dict[str, int]] = None,
        target_robot_id: Optional[str] = None,
        host: str = "127.0.0.1",
        secret_key: str = DEFAULT_SECRET_KEY,
        station_role: Optional[str] = None,
        station_id: Optional[str] = None,
        current_tick: int = 0,
    ) -> Optional[str]:
        """
        Dispatches a task to the decentralized fleet.
        If target_robot_id is provided, sends signed unicast assignment (backward compatibility).
        Otherwise, broadcasts signed TASK_ANNOUNCEMENT to all robots of eligible type without telemetry dependencies.
        Robots evaluate contract-net eligibility and submit bids independently.
        """
        ports = peer_ports or get_fleet_peer_ports()

        # Backward compatibility unicast if explicit robot specified
        if target_robot_id is not None:
            envelope = build_task_assignment_envelope(
                task,
                target_robot_id,
                secret_key=secret_key,
                station_role=station_role,
                station_id=station_id,
            )
            try:
                self._send_envelope_to_robot(target_robot_id, envelope, transport_sender, ports, host)
            except Exception as e:
                log.error("DISPATCH_ERROR sending task %s to %s: %s", task.task_id, target_robot_id, e)
                return None

            task.status = TaskStatus.ASSIGNED
            task.assigned_robot_id = target_robot_id
            task._assigned_tick = current_tick
            task.lease_expires_tick = current_tick + 40
            log.info(
                "TASK_DISPATCHED task_id=%s robot=%s pickup=(%d,%d) dropoff=(%d,%d)",
                task.task_id, target_robot_id, task.pickup_x, task.pickup_y, task.dropoff_x, task.dropoff_y,
            )
            return target_robot_id

        # Decentralized Contract-Net: Broadcast signed TASK_ANNOUNCEMENT
        envelope = build_task_announcement_envelope(
            task,
            secret_key=secret_key,
            station_role=station_role,
            station_id=station_id,
        )

        task_type_str = task.task_type.value if hasattr(task.task_type, "value") else str(getattr(task, "task_type", "STANDARD"))
        eligible_rids = []
        try:
            from app.models.world import get_active_map_data
            map_dict = get_active_map_data()
            r_specs = map_dict.get("robot_starts", []) if map_dict else []
            for r_spec in r_specs:
                rid = r_spec.get("id")
                rtype = r_spec.get("type") or r_spec.get("robot_type", "GOODS_TO_PERSON")
                if task_type_str in ("RETRIEVE_POD", "RETURN_POD", "PICK_ITEM") and rtype != "GOODS_TO_PERSON":
                    continue
                if task_type_str in ("INDUCT_BATCH", "DECANT_TO_CHUTE", "CONSOLIDATE_EXPORT", "TRANSFER_TO_SORTATION") and rtype != "SORTING":
                    continue
                if task_type_str == "AUDIT" and rtype != "SCANNING_AUDIT":
                    continue
                if rid and rid in ports:
                    eligible_rids.append(rid)
        except Exception:
            pass

        if not eligible_rids:
            for rid in ports.keys():
                if rid.endswith("_STATION"):
                    continue
                if task_type_str in ("RETRIEVE_POD", "RETURN_POD", "PICK_ITEM") and (rid.startswith("SORT-") or rid.startswith("AUDIT-")):
                    continue
                if task_type_str in ("INDUCT_BATCH", "DECANT_TO_CHUTE", "CONSOLIDATE_EXPORT", "TRANSFER_TO_SORTATION") and (rid.startswith("G2P-") or rid.startswith("AUDIT-")):
                    continue
                if task_type_str == "AUDIT" and (rid.startswith("G2P-") or rid.startswith("SORT-")):
                    continue
                eligible_rids.append(rid)

        if not eligible_rids:
            eligible_rids = [p for p in ports.keys() if not p.endswith("_STATION")]

        broadcast_count = 0
        for rid in eligible_rids:
            try:
                self._send_envelope_to_robot(rid, envelope, transport_sender, ports, host)
                broadcast_count += 1
            except Exception as e:
                log.debug("Failed sending TASK_ANNOUNCEMENT to %s: %s", rid, e)

        task.status = TaskStatus.ANNOUNCED
        task.last_announced_tick = current_tick
        task.announcement_count += 1

        if broadcast_count > 0:
            log.debug(
                "TASK_ANNOUNCED task_id=%s broadcasted to %d eligible robots pickup=(%d,%d) dropoff=(%d,%d)",
                task.task_id, broadcast_count, task.pickup_x, task.pickup_y, task.dropoff_x, task.dropoff_y,
            )
            return f"ANNOUNCED_{broadcast_count}"

        return None



