"""
Task REST endpoints.

POST /api/task/inject — SCHEMA.md §17
GET  /api/tasks       — list all tasks
GET  /api/tasks/{id}  — get single task
"""

from __future__ import annotations

import time
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas.task import JobOut, JobRequest, TaskInjectRequest, TaskOut
from app.models.obstacle import TemporaryObstacle
from app.models.robot import AMRType, RobotState

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/task", tags=["Tasks"])
job_router = APIRouter(prefix="/api", tags=["Jobs"])


def _get_engine(request: Request):
    return request.app.state.engine


def _get_fleet(request: Request):
    return request.app.state.fleet_state


def _get_task_manager(request: Request):
    return request.app.state.task_manager


def _get_tel(request: Request):
    return request.app.state.telemetry


@router.post(
    "/inject",
    summary="Inject a new warehouse task",
    description=(
        "Creates a new pickup → dropoff task and assigns it to an available robot. "
        "Returns quickly — does not block waiting for a simulation tick. "
        "SCHEMA.md §17."
    ),
    response_model=TaskOut,
    status_code=201,
)
async def inject_task(
    body: TaskInjectRequest,
    request: Request,
) -> TaskOut:
    t0 = time.monotonic()
    fleet = _get_fleet(request)
    task_manager = _get_task_manager(request)
    tel = _get_tel(request)

    # Validate coordinates are not static obstacles or out of bounds
    world = fleet.world
    if not world.in_bounds(body.pickup.x, body.pickup.y):
        raise HTTPException(400, f"Pickup {body.pickup} out of bounds")
    if not world.in_bounds(body.dropoff.x, body.dropoff.y):
        raise HTTPException(400, f"Dropoff {body.dropoff} out of bounds")
    if world.is_static_blocked(body.pickup.x, body.pickup.y):
        raise HTTPException(400, f"Pickup {body.pickup} is a static obstacle")
    if world.is_static_blocked(body.dropoff.x, body.dropoff.y):
        raise HTTPException(400, f"Dropoff {body.dropoff} is a static obstacle")
    if (body.pickup.x, body.pickup.y) == (body.dropoff.x, body.dropoff.y):
        raise HTTPException(400, "Pickup and dropoff cannot be the same cell")

    # Create task and queue it into the simulation state
    task = task_manager.create_task(
        pickup_x=body.pickup.x,
        pickup_y=body.pickup.y,
        dropoff_x=body.dropoff.x,
        dropoff_y=body.dropoff.y,
        urgency=body.urgency,
        current_tick=fleet.tick,
    )
    fleet.queue_task(task)

    # Attempt immediate dispatch to available robot via UDP
    from app.services.task_manager import get_fleet_peer_ports
    orchestrator = getattr(request.app.state, "orchestrator", None)
    peer_ports = get_fleet_peer_ports(orchestrator)
    task_manager.dispatch_to_fleet(task, peer_ports=peer_ports)

    injection_ms = (time.monotonic() - t0) * 1000
    tel.record_task_injection(injection_ms)

    log.info(
        "TASK_INJECTED task_id=%s status=%s assigned_to=%s latency_ms=%.2f",
        task.task_id, task.status.value, task.assigned_robot_id, injection_ms,
    )

    return TaskOut(
        task_id=task.task_id,
        pickup={"x": task.pickup_x, "y": task.pickup_y},
        dropoff={"x": task.dropoff_x, "y": task.dropoff_y},
        urgency=task.urgency,
        status=task.status.value,
        assigned_robot_id=task.assigned_robot_id,
        created_tick=task.created_tick,
    )


@router.get("/all", summary="List all tasks", response_model=List[TaskOut])
async def list_tasks(request: Request) -> List[TaskOut]:
    task_manager = _get_task_manager(request)
    return [
        TaskOut(
            task_id=t.task_id,
            pickup={"x": t.pickup_x, "y": t.pickup_y},
            dropoff={"x": t.dropoff_x, "y": t.dropoff_y},
            urgency=t.urgency,
            status=t.status.value,
            assigned_robot_id=t.assigned_robot_id,
            created_tick=t.created_tick,
        )
        for t in task_manager.all_tasks().values()
    ]


@router.get("/{task_id}", summary="Get a single task", response_model=TaskOut)
async def get_task(task_id: str, request: Request) -> TaskOut:
    task_manager = _get_task_manager(request)
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task {task_id!r} not found")
    return TaskOut(
        task_id=task.task_id,
        pickup={"x": task.pickup_x, "y": task.pickup_y},
        dropoff={"x": task.dropoff_x, "y": task.dropoff_y},
        urgency=task.urgency,
        status=task.status.value,
        assigned_robot_id=task.assigned_robot_id,
        created_tick=task.created_tick,
    )


def _resolve_job_points(world, job_type: str, zone: str | None = None):
    if job_type == "fetch_item":
        shelf_cells = [
            (x, y)
            for x in range(world.width)
            for y in range(world.height)
            if 7 <= x <= 22 and 8 <= y <= 22 and (x, y) not in world.static_obstacles
        ]
        pickup = shelf_cells[0] if shelf_cells else (11, 12)
        dropoff = min(world.dropoff_stations, key=lambda p: (p[0], p[1])) if world.dropoff_stations else (27, 17)
        return pickup, dropoff, AMRType.GOODS_TO_PERSON

    if job_type == "sort_batch":
        pickup = min(world.pickup_stations, key=lambda p: (p[0], p[1])) if world.pickup_stations else (1, 10)
        sorting_candidates = [
            (x, y)
            for x in range(world.width)
            for y in range(world.height)
            if world.zone_for(x, y) == "SORTING_ZONE" and (x, y) not in world.static_obstacles
        ]
        dropoff = sorting_candidates[0] if sorting_candidates else (12, 25)
        return pickup, dropoff, AMRType.SORTING

    if job_type == "audit_checkpoint":
        checkpoint_candidates = [
            (x, y)
            for x in range(world.width)
            for y in range(world.height)
            if (x, y) not in world.static_obstacles
        ]
        checkpoint = min(checkpoint_candidates, key=lambda p: abs(p[0] - 15) + abs(p[1] - 15)) if checkpoint_candidates else (15, 15)
        return checkpoint, checkpoint, AMRType.SCANNING_AUDIT

    raise HTTPException(400, f"Unsupported job_type: {job_type}")


def _pick_idle_robot_for_type(fleet, robot_type: AMRType, target: tuple[int, int] | None = None):
    candidates = [
        robot for robot in fleet.robots.values()
        if robot.state == RobotState.IDLE and robot.robot_type == robot_type
    ]
    if not candidates:
        return None
    if target is None:
        return min(candidates, key=lambda robot: robot.robot_id)
    return min(candidates, key=lambda robot: abs(robot.x - target[0]) + abs(robot.y - target[1]))


@job_router.post(
    "/job",
    summary="Create a user-facing warehouse job",
    response_model=JobOut,
    status_code=200,
)
async def create_job(body: JobRequest, request: Request) -> JobOut:
    fleet = _get_fleet(request)
    task_manager = _get_task_manager(request)
    from app.services.task_manager import get_fleet_peer_ports
    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))

    if body.job_type == "fetch_item":
        pickup, dropoff, robot_type = _resolve_job_points(fleet.world, "fetch_item")
        selected_robot = _pick_idle_robot_for_type(fleet, robot_type, pickup)
        if selected_robot is None:
            raise HTTPException(409, "No GOODS_TO_PERSON robot available for fetch_item job")
        task = task_manager.create_task(
            pickup_x=pickup[0],
            pickup_y=pickup[1],
            dropoff_x=dropoff[0],
            dropoff_y=dropoff[1],
            urgency=body.urgency,
            current_tick=fleet.tick,
        )
        fleet.queue_task(task)
        assigned_robot = task_manager.try_assign(task, {selected_robot.robot_id: selected_robot}, fleet.tick)
        if not assigned_robot:
            raise HTTPException(409, "No GOODS_TO_PERSON robot available for fetch_item job")
        task_manager.dispatch_to_fleet(task, peer_ports=peer_ports, target_robot_id=assigned_robot)
        return JobOut(
            job_type=body.job_type,
            robot_type=robot_type.value,
            task_id=task.task_id,
            robot_id=assigned_robot,
            status=task.status.value,
            message="Fetch item job assigned to a GOODS_TO_PERSON robot",
        )

    if body.job_type == "sort_batch":
        pickup, dropoff, robot_type = _resolve_job_points(fleet.world, "sort_batch")
        selected_robot = _pick_idle_robot_for_type(fleet, robot_type, pickup)
        if selected_robot is None:
            raise HTTPException(409, "No SORTING robot available for sort_batch job")
        task = task_manager.create_task(
            pickup_x=pickup[0],
            pickup_y=pickup[1],
            dropoff_x=dropoff[0],
            dropoff_y=dropoff[1],
            urgency=body.urgency,
            current_tick=fleet.tick,
        )
        fleet.queue_task(task)
        assigned_robot = task_manager.try_assign(task, {selected_robot.robot_id: selected_robot}, fleet.tick)
        if not assigned_robot:
            raise HTTPException(409, "No SORTING robot available for sort_batch job")
        task_manager.dispatch_to_fleet(task, peer_ports=peer_ports, target_robot_id=assigned_robot)
        return JobOut(
            job_type=body.job_type,
            robot_type=robot_type.value,
            task_id=task.task_id,
            robot_id=assigned_robot,
            status=task.status.value,
            message="Sort batch job assigned to a SORTING robot",
        )

    if body.job_type == "audit_checkpoint":
        checkpoint, _, robot_type = _resolve_job_points(fleet.world, "audit_checkpoint")
        selected_robot = _pick_idle_robot_for_type(fleet, AMRType.SCANNING_AUDIT, checkpoint)
        if selected_robot is None:
            raise HTTPException(409, "No SCANNING_AUDIT robot available for audit_checkpoint job")
        from app.services.audit_mission import AuditMission
        from app.models.task import Task, TaskStatus
        mission = AuditMission(checkpoint, audit_id=f"AUDIT-{selected_robot.robot_id}")
        audit_task = Task(
            task_id=mission.audit_id,
            pickup_x=selected_robot.x,
            pickup_y=selected_robot.y,
            dropoff_x=checkpoint[0],
            dropoff_y=checkpoint[1],
            urgency=body.urgency,
            created_tick=fleet.tick,
            status=TaskStatus.ASSIGNED,
            assigned_robot_id=selected_robot.robot_id,
        )
        task_manager._tasks[audit_task.task_id] = audit_task
        fleet.tasks[audit_task.task_id] = audit_task
        selected_robot.current_task_id = mission.audit_id
        selected_robot.path = []
        selected_robot.state = RobotState.EN_ROUTE
        task_manager.dispatch_to_fleet(audit_task, peer_ports=peer_ports, target_robot_id=selected_robot.robot_id)
        return JobOut(
            job_type=body.job_type,
            robot_type=robot_type.value,
            audit_id=mission.audit_id,
            robot_id=selected_robot.robot_id,
            status="AUDIT_SCHEDULED",
            message="Audit checkpoint job scheduled on a SCANNING_AUDIT robot",
        )

    raise HTTPException(400, f"Unsupported job_type: {body.job_type}")
