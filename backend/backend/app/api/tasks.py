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

from app.schemas.task import JobOut, JobRequest, OrderRequest, TaskInjectRequest, TaskOut
from app.models.obstacle import TemporaryObstacle
from app.models.robot import AMRType, Robot, RobotState
from app.models.task import Task, TaskStatus, TaskType
from app.models.inventory import ShelfRecord
from app.services.inventory_ledger import InventoryLedger

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

    # Durable Write-Ahead Journal entry (SPOF Hardening)
    journal = getattr(request.app.state, "job_journal", None)
    if journal:
        journal.log_submission(
            job_id=task.task_id,
            job_type="custom_task",
            pickup=(task.pickup_x, task.pickup_y),
            dropoff=(task.dropoff_x, task.dropoff_y),
            urgency=task.urgency,
            payload_weight_kg=getattr(task, "payload_weight_kg", 0.0),
        )

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


_job_dispatch_counter = 0


def _resolve_job_points(world, job_type: str, zone: str | None = None):
    global _job_dispatch_counter

    if job_type == "fetch_item":
        if _job_dispatch_counter == 0:
            pickup = (11, 12)
        else:
            shelf_cells = [
                (x, y)
                for x in range(world.width)
                for y in range(world.height)
                if 6 <= x <= 23 and 8 <= y <= 21 and (x, y) not in world.static_obstacles
            ]
            pickup = shelf_cells[_job_dispatch_counter % len(shelf_cells)] if shelf_cells else (11, 12)
        _job_dispatch_counter += 1
        dropoffs = sorted(list(world.dropoff_stations))
        dropoff = dropoffs[_job_dispatch_counter % len(dropoffs)] if dropoffs else (29, 9)
        return pickup, dropoff, AMRType.GOODS_TO_PERSON

    if job_type == "sort_batch":
        pickups = sorted(list(world.pickup_stations))
        pickup = pickups[_job_dispatch_counter % len(pickups)] if pickups else (0, 9)
        sorting_candidates = [
            (x, y)
            for x in range(world.width)
            for y in range(world.height)
            if world.zone_for(x, y) == "SORTING_ZONE" and (x, y) not in world.static_obstacles
        ]
        dropoff = sorting_candidates[_job_dispatch_counter % len(sorting_candidates)] if sorting_candidates else (12, 25)
        return pickup, dropoff, AMRType.SORTING

    if job_type == "audit_checkpoint":
        from app.services.audit_mission import DEFAULT_CHECKPOINTS
        cps = list(DEFAULT_CHECKPOINTS)
        checkpoint = cps[_job_dispatch_counter % len(cps)]
        return checkpoint, checkpoint, AMRType.SCANNING_AUDIT

    raise HTTPException(400, f"Unsupported job_type: {job_type}")



def select_best_shelf_for_sku(
    ledger: InventoryLedger,
    sku: str,
    quantity: int = 1,
    idle_g2p_robots: Optional[List[Robot]] = None,
    current_tick: Optional[int] = None,
) -> Optional[ShelfRecord]:
    """
    Selects the optimal shelf holding the requested SKU per FIX 4:
      1. Prefer highest confidence (decayed according to current_tick).
      2. Break ties by minimum Manhattan distance to an idle G2P robot if available.
      3. Break ties by lowest last_audited_tick (nudges coverage toward stale shelves).
      4. Deterministic tie-break by shelf_id.
    """
    candidates = ledger.get_shelves_for_sku(sku, min_qty=quantity, current_tick=current_tick)
    if not candidates:
        return None

    def sort_key(rec: ShelfRecord):
        conf = rec.confidence
        if idle_g2p_robots:
            min_dist = min(abs(r.x - rec.x) + abs(r.y - rec.y) for r in idle_g2p_robots)
        else:
            min_dist = 0
        audited_tick = rec.last_audited_tick
        return (-conf, min_dist, audited_tick, rec.shelf_id)

    candidates.sort(key=sort_key)
    return candidates[0]


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
        requested_sku = body.sku or body.item_id
        if requested_sku:
            ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()
            idle_g2ps = [
                robot for robot in fleet.robots.values()
                if robot.state == RobotState.IDLE and robot.robot_type == AMRType.GOODS_TO_PERSON
            ]
            best_shelf = select_best_shelf_for_sku(
                ledger, requested_sku, quantity=body.quantity,
                idle_g2p_robots=idle_g2ps, current_tick=fleet.tick,
            )
            if best_shelf is None:
                raise HTTPException(404, f"No shelf holds requested SKU {requested_sku!r} with quantity >= {body.quantity}")

            pickup = (best_shelf.x, best_shelf.y)
            dropoffs = sorted(list(fleet.world.dropoff_stations))
            dropoff = dropoffs[0] if dropoffs else (29, 9)
            robot_type = AMRType.GOODS_TO_PERSON
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
                task_type=TaskType.RETRIEVE_POD,
                target_shelf_id=best_shelf.shelf_id,
                sku_to_pick=requested_sku,
                quantity=body.quantity,
            )
            fleet.queue_task(task)
            assigned_robot = task_manager.try_assign(task, {selected_robot.robot_id: selected_robot}, fleet.tick)
            if not assigned_robot:
                raise HTTPException(409, "No GOODS_TO_PERSON robot available for fetch_item job")

            journal = getattr(request.app.state, "job_journal", None)
            if journal:
                journal.log_submission(
                    job_id=task.task_id,
                    job_type=body.job_type,
                    pickup=pickup,
                    dropoff=dropoff,
                    urgency=body.urgency,
                )
                journal.log_assignment(
                    job_id=task.task_id,
                    assigned_robot_id=assigned_robot,
                    tick=fleet.tick,
                )

            task_manager.dispatch_to_fleet(task, peer_ports=peer_ports, target_robot_id=assigned_robot)
            return JobOut(
                job_type=body.job_type,
                robot_type=robot_type.value,
                task_id=task.task_id,
                robot_id=assigned_robot,
                target_shelf_id=best_shelf.shelf_id,
                sku=requested_sku,
                quantity=body.quantity,
                status=task.status.value,
                message=f"Fetch item job for SKU {requested_sku} assigned to {assigned_robot} (Shelf {best_shelf.shelf_id})",
            )

        # Legacy fallback without SKU
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

        # Write-ahead commit to journal
        journal = getattr(request.app.state, "job_journal", None)
        if journal:
            journal.log_submission(
                job_id=task.task_id,
                job_type=body.job_type,
                pickup=pickup,
                dropoff=dropoff,
                urgency=body.urgency,
            )
            journal.log_assignment(
                job_id=task.task_id,
                assigned_robot_id=assigned_robot,
                tick=fleet.tick,
            )

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

        # Write-ahead commit to journal
        journal = getattr(request.app.state, "job_journal", None)
        if journal:
            journal.log_submission(
                job_id=task.task_id,
                job_type=body.job_type,
                pickup=pickup,
                dropoff=dropoff,
                urgency=body.urgency,
            )
            journal.log_assignment(
                job_id=task.task_id,
                assigned_robot_id=assigned_robot,
                tick=fleet.tick,
            )

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

        # Write-ahead commit to journal
        journal = getattr(request.app.state, "job_journal", None)
        if journal:
            journal.log_submission(
                job_id=mission.audit_id,
                job_type=body.job_type,
                pickup=checkpoint,
                dropoff=checkpoint,
                urgency=body.urgency,
            )
            journal.log_assignment(
                job_id=mission.audit_id,
                assigned_robot_id=selected_robot.robot_id,
                tick=fleet.tick,
            )

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


@router.post(
    "/order",
    summary="Create a SKU-based order for G2P pod retrieval",
    response_model=JobOut,
    status_code=201,
)
async def create_sku_order(body: OrderRequest, request: Request) -> JobOut:
    """
    Direct SKU order endpoint. Queries InventoryLedger for the optimal shelf holding
    the requested SKU and dispatches a RETRIEVE_POD task to an idle G2P AMR.
    """
    fleet = _get_fleet(request)
    task_manager = _get_task_manager(request)
    from app.services.task_manager import get_fleet_peer_ports
    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))
    ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()

    idle_g2ps = [
        robot for robot in fleet.robots.values()
        if robot.state == RobotState.IDLE and robot.robot_type == AMRType.GOODS_TO_PERSON
    ]
    best_shelf = select_best_shelf_for_sku(
        ledger, body.sku, quantity=body.quantity,
        idle_g2p_robots=idle_g2ps, current_tick=fleet.tick,
    )
    if best_shelf is None:
        raise HTTPException(404, f"No shelf holds SKU {body.sku!r} with quantity >= {body.quantity}")

    pickup = (best_shelf.x, best_shelf.y)
    if body.dropoff:
        dropoff = (body.dropoff.x, body.dropoff.y)
    else:
        dropoffs = sorted(list(fleet.world.dropoff_stations))
        dropoff = dropoffs[0] if dropoffs else (29, 9)

    selected_robot = _pick_idle_robot_for_type(fleet, AMRType.GOODS_TO_PERSON, pickup)
    if selected_robot is None:
        raise HTTPException(409, "No GOODS_TO_PERSON robot available for order")

    task = task_manager.create_task(
        pickup_x=pickup[0],
        pickup_y=pickup[1],
        dropoff_x=dropoff[0],
        dropoff_y=dropoff[1],
        urgency=body.urgency,
        current_tick=fleet.tick,
        task_type=TaskType.RETRIEVE_POD,
        target_shelf_id=best_shelf.shelf_id,
        sku_to_pick=body.sku,
        quantity=body.quantity,
    )
    fleet.queue_task(task)
    assigned_robot = task_manager.try_assign(task, {selected_robot.robot_id: selected_robot}, fleet.tick)
    if not assigned_robot:
        raise HTTPException(409, "No GOODS_TO_PERSON robot available for order")

    journal = getattr(request.app.state, "job_journal", None)
    if journal:
        journal.log_submission(
            job_id=task.task_id,
            job_type="fetch_item",
            pickup=pickup,
            dropoff=dropoff,
            urgency=body.urgency,
        )
        journal.log_assignment(
            job_id=task.task_id,
            assigned_robot_id=assigned_robot,
            tick=fleet.tick,
        )

    task_manager.dispatch_to_fleet(task, peer_ports=peer_ports, target_robot_id=assigned_robot)
    return JobOut(
        job_type="fetch_item",
        robot_type=AMRType.GOODS_TO_PERSON.value,
        task_id=task.task_id,
        robot_id=assigned_robot,
        target_shelf_id=best_shelf.shelf_id,
        sku=body.sku,
        quantity=body.quantity,
        status=task.status.value,
        message=f"Order for SKU {body.sku} assigned to {assigned_robot} (Shelf {best_shelf.shelf_id})",
    )


@job_router.post(
    "/job/order",
    summary="Create a SKU-based order via /api/job/order",
    response_model=JobOut,
    status_code=201,
)
async def create_sku_job_order(body: OrderRequest, request: Request) -> JobOut:
    return await create_sku_order(body, request)


@job_router.post(
    "/order",
    summary="Create a SKU-based order via /api/order",
    response_model=JobOut,
    status_code=201,
)
async def create_sku_api_order(body: OrderRequest, request: Request) -> JobOut:
    return await create_sku_order(body, request)

