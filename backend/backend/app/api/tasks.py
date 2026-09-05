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

from app.schemas.task import TaskInjectRequest, TaskOut
from app.models.obstacle import TemporaryObstacle

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/task", tags=["Tasks"])


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

    injection_ms = (time.monotonic() - t0) * 1000
    tel.record_task_injection(injection_ms)

    log.info(
        "TASK_CREATED task_id=%s urgency=%d pickup=(%d,%d) dropoff=(%d,%d) latency_ms=%.2f",
        task.task_id, task.urgency, task.pickup_x, task.pickup_y,
        task.dropoff_x, task.dropoff_y, injection_ms,
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
