"""
Task REST endpoints.

POST /api/task/inject — SCHEMA.md §17
GET  /api/tasks       — list all tasks
GET  /api/tasks/{id}  — get single task
"""

from __future__ import annotations

import time
import uuid
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

    # Operator Role Verification & Authorization (Step 4 RBAC)
    op_role = request.headers.get("x-operator-role", "AUTHORITY").upper()
    if op_role == "IMPORT":
        if not (body.pickup.x <= 2 and 8 <= body.pickup.y <= 20):
            raise HTTPException(
                403,
                f"STATION_AUTHORITY_VIOLATION: Operator with role 'IMPORT' cannot inject tasks outside West import dock (pickup=({body.pickup.x}, {body.pickup.y}))."
            )
    elif op_role == "EXPORT":
        if body.pickup.x <= 2 and 8 <= body.pickup.y <= 20:
            raise HTTPException(
                403,
                f"STATION_AUTHORITY_VIOLATION: Operator with role 'EXPORT' cannot inject tasks originating from West import dock (pickup=({body.pickup.x}, {body.pickup.y}))."
            )

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
        if world.pod_slots:
            shelf_cells = sorted(list(world.pod_slots.values()))
            pickup = shelf_cells[_job_dispatch_counter % len(shelf_cells)]
        else:
            shelf_cells = [
                (x, y)
                for x in range(world.width)
                for y in range(world.height)
                if world.zone_for(x, y) == "GOODS_TO_PERSON_ZONE" and (x, y) not in world.static_obstacles
            ]
            pickup = shelf_cells[_job_dispatch_counter % len(shelf_cells)] if shelf_cells else (world.width // 4, world.height // 2)
        _job_dispatch_counter += 1
        dropoffs = sorted(list(world.dropoff_stations))
        dropoff = dropoffs[_job_dispatch_counter % len(dropoffs)] if dropoffs else (world.width - 1, world.height // 2)
        return pickup, dropoff, AMRType.GOODS_TO_PERSON

    if job_type == "sort_batch":
        pickups = sorted(list(world.pickup_stations))
        pickup = pickups[_job_dispatch_counter % len(pickups)] if pickups else (0, world.height // 2)
        if world.sortation_chutes:
            chutes = sorted([(c["x"], c["y"]) for c in world.sortation_chutes.values()])
            dropoff = chutes[_job_dispatch_counter % len(chutes)]
        else:
            sorting_candidates = [
                (x, y)
                for x in range(world.width)
                for y in range(world.height)
                if world.zone_for(x, y) == "SORTING_ZONE" and (x, y) not in world.static_obstacles
            ]
            dropoff = sorting_candidates[_job_dispatch_counter % len(sorting_candidates)] if sorting_candidates else (world.width - 2, world.height // 2)
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
    excluded_shelf_ids: Optional[Set[str]] = None,
) -> Optional[ShelfRecord]:
    """
    Selects the optimal shelf holding the requested SKU per FIX 4:
      1. Prefer highest confidence (decayed according to current_tick).
      2. Break ties by minimum Manhattan distance to an idle G2P robot if available.
      3. Break ties by lowest last_audited_tick (nudges coverage toward stale shelves).
      4. Deterministic tie-break by shelf_id.
    """
    candidates = ledger.get_shelves_for_sku(sku, min_qty=quantity, current_tick=current_tick)
    if excluded_shelf_ids:
        filtered = [c for c in candidates if c.shelf_id not in excluded_shelf_ids]
        if filtered:
            candidates = filtered
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


@job_router.post(
    "/job",
    summary="Create a user-facing warehouse job",
    response_model=JobOut,
    status_code=200,
)
async def create_job(body: JobRequest, request: Request) -> JobOut:
    """
    Decentralized task injection for user jobs.
    The API validates bounds/RBAC, creates the Task, commits to durable journal,
    and broadcasts signed TASK_ANNOUNCEMENT over UDP. Robots independently bid and claim.
    """
    fleet = _get_fleet(request)
    task_manager = _get_task_manager(request)
    from app.services.task_manager import get_fleet_peer_ports
    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))

    op_role = request.headers.get("x-operator-role", "AUTHORITY").upper()
    if op_role == "IMPORT" and body.job_type == "sort_batch":
        raise HTTPException(
            403,
            "STATION_AUTHORITY_VIOLATION: Operator with role 'IMPORT' cannot issue 'sort_batch' jobs. Restricted to Export Station.",
        )
    if op_role == "EXPORT" and body.job_type == "fetch_item" and getattr(body, "zone", "") in ("IMPORT_DOCK", "IN-1", "IN-2", "IN-3"):
        raise HTTPException(
            403,
            "STATION_AUTHORITY_VIOLATION: Operator with role 'EXPORT' cannot issue inbound 'fetch_item' jobs for import dock. Restricted to Import Station.",
        )

    world = fleet.world

    if body.job_type == "fetch_item":
        requested_sku = body.sku or body.item_id
        target_shelf = None
        if requested_sku:
            ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()
            best_shelf = select_best_shelf_for_sku(
                ledger, requested_sku, quantity=body.quantity,
                idle_g2p_robots=None, current_tick=fleet.tick,
            )
            if best_shelf is None:
                raise HTTPException(404, f"No shelf holds requested SKU {requested_sku!r} with quantity >= {body.quantity}")
            pickup = (best_shelf.x, best_shelf.y)
            target_shelf = best_shelf.shelf_id
        elif body.shelf_id:
            target_shelf = body.shelf_id
            pos = world.pod_slots.get(target_shelf) if hasattr(world, "pod_slots") else None
            if not pos:
                ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()
                shelf_rec = ledger.get_shelf(target_shelf)
                if shelf_rec:
                    pos = (shelf_rec.x, shelf_rec.y)
            if not pos:
                raise HTTPException(404, f"Shelf {target_shelf!r} not found on map")
            pickup = pos
        else:
            pickup, _, _ = _resolve_job_points(world, "fetch_item")
            target_shelf = world.shelf_at(pickup[0], pickup[1]) or "POD-01"

        if body.dropoff:
            dropoff = (body.dropoff.x, body.dropoff.y)
        else:
            dropoffs = sorted(list(world.dropoff_stations))
            dropoff = dropoffs[0] if dropoffs else (world.width - 1, world.height // 2)

        task = task_manager.create_task(
            pickup_x=pickup[0],
            pickup_y=pickup[1],
            dropoff_x=dropoff[0],
            dropoff_y=dropoff[1],
            urgency=body.urgency,
            current_tick=fleet.tick,
            task_type=TaskType.RETRIEVE_POD,
            target_shelf_id=target_shelf,
            sku_to_pick=requested_sku,
            quantity=body.quantity,
            return_to_home=body.return_to_home,
            home_slot=pickup,
        )
        task.status = TaskStatus.ANNOUNCED
        fleet.queue_task(task)

        journal = getattr(request.app.state, "job_journal", None)
        if journal:
            journal.log_submission(
                job_id=task.task_id,
                job_type=body.job_type,
                pickup=pickup,
                dropoff=dropoff,
                urgency=body.urgency,
            )

        task_manager.dispatch_to_fleet(
            task,
            peer_ports=peer_ports,
            station_role=f"{op_role}_STATION",
            current_tick=fleet.tick,
        )

        return JobOut(
            job_type=body.job_type,
            robot_type=AMRType.GOODS_TO_PERSON.value,
            task_id=task.task_id,
            robot_id="PENDING",
            target_shelf_id=target_shelf,
            sku=requested_sku,
            quantity=body.quantity,
            status=task.status.value,
            message=f"Fetch item job announced for decentralized bidding (Shelf {target_shelf})",
        )

    if body.job_type == "sort_batch":
        # Resolve source gate or pickup
        if body.source_gate and hasattr(world, "entry_gates") and body.source_gate in world.entry_gates:
            g = world.entry_gates[body.source_gate]
            pickup = (g["x"], g["y"])
        elif body.pickup:
            pickup = (body.pickup.x, body.pickup.y)
        else:
            pickup, _, _ = _resolve_job_points(world, "sort_batch")

        # Resolve destination chute
        if body.destination_chute and hasattr(world, "sortation_chutes") and body.destination_chute in world.sortation_chutes:
            c = world.sortation_chutes[body.destination_chute]
            dropoff = (c["x"], c["y"])
        elif body.dropoff:
            dropoff = (body.dropoff.x, body.dropoff.y)
        else:
            _, dropoff, _ = _resolve_job_points(world, "sort_batch")

        route_code = body.route_code or body.item_id or "ROUTE-01"

        task = task_manager.create_task(
            pickup_x=pickup[0],
            pickup_y=pickup[1],
            dropoff_x=dropoff[0],
            dropoff_y=dropoff[1],
            urgency=body.urgency,
            current_tick=fleet.tick,
            task_type=TaskType.INDUCT_BATCH,
            destination_zone=body.zone or "SORTING_ZONE",
            route_code=route_code,
        )
        task.status = TaskStatus.ANNOUNCED
        fleet.queue_task(task)

        journal = getattr(request.app.state, "job_journal", None)
        if journal:
            journal.log_submission(
                job_id=task.task_id,
                job_type=body.job_type,
                pickup=pickup,
                dropoff=dropoff,
                urgency=body.urgency,
            )

        task_manager.dispatch_to_fleet(
            task,
            peer_ports=peer_ports,
            station_role=f"{op_role}_STATION",
            current_tick=fleet.tick,
        )

        return JobOut(
            job_type=body.job_type,
            robot_type=AMRType.SORTING.value,
            task_id=task.task_id,
            robot_id="PENDING",
            status=task.status.value,
            message="Sort batch job announced for decentralized bidding by SORTING AMRs",
        )

    if body.job_type == "audit_checkpoint":
        if body.checkpoint:
            checkpoint = (body.checkpoint.x, body.checkpoint.y)
        elif body.shelf_id and hasattr(world, "pod_slots") and body.shelf_id in world.pod_slots:
            checkpoint = world.pod_slots[body.shelf_id]
        else:
            checkpoint, _, _ = _resolve_job_points(world, "audit_checkpoint")

        task = task_manager.create_task(
            pickup_x=checkpoint[0],
            pickup_y=checkpoint[1],
            dropoff_x=checkpoint[0],
            dropoff_y=checkpoint[1],
            urgency=body.urgency,
            current_tick=fleet.tick,
            task_type=TaskType.AUDIT,
            target_shelf_id=body.shelf_id,
        )
        task.status = TaskStatus.ANNOUNCED
        fleet.tasks[task.task_id] = task
        fleet.queue_task(task)

        journal = getattr(request.app.state, "job_journal", None)
        if journal:
            journal.log_submission(
                job_id=task.task_id,
                job_type=body.job_type,
                pickup=checkpoint,
                dropoff=checkpoint,
                urgency=body.urgency,
            )

        task_manager.dispatch_to_fleet(
            task,
            peer_ports=peer_ports,
            station_role=f"{op_role}_STATION",
            current_tick=fleet.tick,
        )

        return JobOut(
            job_type=body.job_type,
            robot_type=AMRType.SCANNING_AUDIT.value,
            task_id=task.task_id,
            audit_id=task.task_id,
            robot_id="PENDING",
            status=task.status.value,
            message="Audit mission announced for decentralized bidding by SCANNING_AUDIT robots",
        )

    raise HTTPException(400, f"Unsupported job_type: {body.job_type}")


@job_router.post(
    "/order",
    summary="Create a SKU-based user order for autonomous fulfillment",
    response_model=JobOut,
    status_code=201,
)
async def create_sku_order(body: OrderRequest, request: Request) -> JobOut:
    """
    Consolidated user order endpoint (Step 5).
    Rejects early:
      - Unknown product
      - Insufficient stock (showing available quantity)
      - No G2P or Sorting robot in current fleet
      - Destination gate unreachable / no feeding chute
    Automated multi-agent pipeline:
      1. G2P claims retrieval, brings pod to nearest free pick station.
      2. Item pick produces Carton tagged with order_id and destination.
      3. Sorting AMR claims carton from pick-station buffer, transfers to chute.
      4. Consolidation delivers carton to destination gate (SHIPPED).
      5. G2P returns pod to home slot.
    """
    fleet = _get_fleet(request)
    task_manager = _get_task_manager(request)
    ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()
    order_manager = getattr(request.app.state, "order_manager", None)
    from app.services.task_manager import get_fleet_peer_ports
    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))

    # 1. Early rejection: Unknown product
    all_shelves = ledger.get_all_shelves()
    known_skus = set()
    for sh in all_shelves:
        known_skus.update(sh.sku_manifest.keys())
    # Also check map catalog
    map_catalog = getattr(fleet.world, "catalog", []) or []
    for item in map_catalog:
        if isinstance(item, dict) and "sku" in item:
            known_skus.add(item["sku"])

    if body.sku not in known_skus:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown product '{body.sku}'. Please select a valid product from the catalog.",
        )

    # 2. Early rejection: Insufficient stock (show available)
    total_available = sum(sh.sku_manifest.get(body.sku, 0) for sh in all_shelves)
    if total_available < body.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient stock for product '{body.sku}': requested {body.quantity}, but only {total_available} available.",
        )

    # 3. Early rejection: Fleet capability check
    # Check running robots in fleet_state or configured robots in world
    active_robots = list(fleet.robots.values())
    has_g2p = any(r.robot_type == AMRType.GOODS_TO_PERSON or str(r.robot_type).endswith("GOODS_TO_PERSON") for r in active_robots)
    has_sorting = any(r.robot_type == AMRType.SORTING or str(r.robot_type).endswith("SORTING") for r in active_robots)

    if not has_g2p:
        raise HTTPException(
            status_code=400,
            detail="Cannot fulfill order: no Goods-to-Person (G2P) robots are configured in this warehouse.",
        )
    if not has_sorting:
        raise HTTPException(
            status_code=400,
            detail="Cannot fulfill order: no Sorting robots are configured in this warehouse.",
        )

    # 4. Early rejection: Destination gate and chute reachability
    dest_gate = body.destination_gate
    if not dest_gate or (dest_gate == "OUT-1" and "OUT-1" not in fleet.world.export_gates):
        dest_gate = list(fleet.world.export_gates.keys())[0] if fleet.world.export_gates else "OUT-1"
    if dest_gate not in fleet.world.export_gates:
        avail_gates = list(fleet.world.export_gates.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Destination exit gate '{dest_gate}' not found in active map. Available gates: {avail_gates}.",
        )

    # Check feeding chute
    chute_id = fleet.world.chute_for_destination(dest_gate)
    if not chute_id:
        raise HTTPException(
            status_code=400,
            detail=f"Destination gate '{dest_gate}' is unreachable or has no feeding sortation chute.",
        )

    # 5. Pod Selection: Avoid targeting same pod twice simultaneously
    active_claimed_shelves = {
        t.target_shelf_id for t in task_manager.all_tasks().values()
        if t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) and t.target_shelf_id
    }
    idle_g2ps = [
        r for r in active_robots
        if (r.robot_type == AMRType.GOODS_TO_PERSON or str(r.robot_type).endswith("GOODS_TO_PERSON"))
        and r.state in (RobotState.IDLE, "IDLE")
        and r.current_task_id is None
    ]

    best_shelf = select_best_shelf_for_sku(
        ledger, body.sku, quantity=body.quantity,
        idle_g2p_robots=idle_g2ps, current_tick=fleet.tick,
        excluded_shelf_ids=active_claimed_shelves,
    )
    if best_shelf is None:
        # Fallback to general best shelf if all have in-flight orders (queueing)
        best_shelf = select_best_shelf_for_sku(
            ledger, body.sku, quantity=body.quantity,
            idle_g2p_robots=idle_g2ps, current_tick=fleet.tick,
        )

    if best_shelf is None:
        raise HTTPException(
            status_code=404,
            detail=f"Could not locate a valid shelf holding SKU '{body.sku}' with quantity >= {body.quantity}.",
        )

    # 6. Nearest Pick Station Dropoff
    pickup = (best_shelf.x, best_shelf.y)
    pick_stations = list(fleet.world.pick_stations.items())
    if pick_stations:
        nearest_ps_id, nearest_ps = min(
            pick_stations,
            key=lambda item: abs(item[1]["x"] - pickup[0]) + abs(item[1]["y"] - pickup[1]),
        )
        dropoff = (nearest_ps["x"], nearest_ps["y"])
        ps_id = nearest_ps.get("id", nearest_ps_id)
    else:
        dropoff = (fleet.world.width - 1, fleet.world.height // 2)
        ps_id = "PICK-01"

    order_id = f"ORD-{uuid.uuid4().hex[:6].upper()}"

    # 7. Create Task
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
        return_to_home=body.return_to_home,
        home_slot=pickup,
    )
    task.order_id = order_id
    task.destination_gate = dest_gate
    task.destination_zone = dest_gate
    task.pick_station_id = ps_id
    task.status = TaskStatus.ANNOUNCED
    fleet.queue_task(task)

    # 8. Register in OrderManager
    from app.services.order_manager import Order, OrderStage
    if order_manager:
        order = Order(
            order_id=order_id,
            sku=body.sku,
            quantity=body.quantity,
            destination_gate=dest_gate,
            urgency=body.urgency,
            created_tick=fleet.tick,
            stage=OrderStage.ANNOUNCED.value,
            shelf_id=best_shelf.shelf_id,
            pick_station_id=ps_id,
            task_ids=[task.task_id],
        )
        order_manager.add_order(order)

    # 9. Journal logging
    journal = getattr(request.app.state, "job_journal", None)
    if journal:
        journal.log_submission(
            job_id=task.task_id,
            job_type="order",
            pickup=pickup,
            dropoff=dropoff,
            urgency=body.urgency,
        )

    # 10. Dispatch to fleet over signed UDP Contract-Net
    task_manager.dispatch_to_fleet(
        task,
        peer_ports=peer_ports,
        current_tick=fleet.tick,
    )

    return JobOut(
        job_type="order",
        robot_type=AMRType.GOODS_TO_PERSON.value,
        order_id=order_id,
        task_id=task.task_id,
        robot_id="PENDING",
        target_shelf_id=best_shelf.shelf_id,
        sku=body.sku,
        quantity=body.quantity,
        destination_gate=dest_gate,
        status=task.status.value,
        message=f"Order {order_id} announced for decentralized bidding (Pod {best_shelf.shelf_id} -> {ps_id} -> {dest_gate})",
    )


# Thin aliases for backward compatibility (flagged for deprecation)
@job_router.post("/job/order", summary="[Deprecated] Alias to /api/order", response_model=JobOut, status_code=201)
async def create_sku_job_order_alias(body: OrderRequest, request: Request) -> JobOut:
    return await create_sku_order(body, request)


@router.post("/order", summary="[Deprecated] Alias to /api/order", response_model=JobOut, status_code=201)
async def create_sku_task_order_alias(body: OrderRequest, request: Request) -> JobOut:
    return await create_sku_order(body, request)


@job_router.get("/orders", summary="Get all active and past orders with stage timelines", status_code=200)
async def list_orders(request: Request) -> List[Dict[str, Any]]:
    """Returns all orders with full timeline stages and robot assignments."""
    order_manager = getattr(request.app.state, "order_manager", None)
    if order_manager:
        return [o.to_dict() for o in order_manager.all_orders()]
    return []


@job_router.get(
    "/tasks/recovery/pending",
    summary="Get pending uncompleted jobs from previous session",
    status_code=200,
)
async def get_pending_recovery_jobs(request: Request):
    pending = getattr(request.app.state, "uncompleted_jobs_pending", [])
    return {"count": len(pending), "jobs": pending}


@job_router.post(
    "/tasks/recovery/resume",
    summary="Resume uncompleted jobs whose coordinates are valid in active world",
    status_code=200,
)
async def resume_recovery_jobs(request: Request):
    fleet = _get_fleet(request)
    task_manager = _get_task_manager(request)
    world = fleet.world
    pending = getattr(request.app.state, "uncompleted_jobs_pending", [])

    from app.services.task_manager import get_fleet_peer_ports
    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))

    resumed_ids = []
    discarded_ids = []

    journal = getattr(request.app.state, "job_journal", None)
    for r_job in list(pending):
        jid = r_job.get("job_id", "")
        pickup = r_job.get("pickup")
        dropoff = r_job.get("dropoff")
        if not pickup or not dropoff or len(pickup) < 2 or len(dropoff) < 2:
            discarded_ids.append(jid)
            if journal and jid:
                journal.log_cancellation(jid, reason="invalid_coordinates")
            continue

        px, py = int(pickup[0]), int(pickup[1])
        dx, dy = int(dropoff[0]), int(dropoff[1])

        # Validate against active world
        if (
            not world.in_bounds(px, py)
            or world.is_static_blocked(px, py)
            or not world.in_bounds(dx, dy)
            or world.is_static_blocked(dx, dy)
        ):
            log.warning("[SPOF RECOVERY] Discarding job %s: coordinates out of bounds or blocked in active world", jid)
            discarded_ids.append(jid)
            if journal and jid:
                journal.log_cancellation(jid, reason="invalid_world_coordinates")
            continue

        t = task_manager.create_task(
            pickup_x=px,
            pickup_y=py,
            dropoff_x=dx,
            dropoff_y=dy,
            urgency=r_job.get("urgency", 3),
            current_tick=fleet.tick,
            task_id=jid,
        )
        if r_job.get("assigned_robot_id"):
            t.assigned_robot_id = r_job["assigned_robot_id"]
        fleet.queue_task(t)
        task_manager.dispatch_to_fleet(t, peer_ports=peer_ports, current_tick=fleet.tick)
        resumed_ids.append(jid)

    request.app.state.uncompleted_jobs_pending = []
    return {
        "resumed_count": len(resumed_ids),
        "discarded_count": len(discarded_ids),
        "resumed_task_ids": resumed_ids,
        "discarded_task_ids": discarded_ids,
    }


@job_router.post(
    "/tasks/recovery/discard",
    summary="Discard all unfinished jobs from previous session and archive journal",
    status_code=200,
)
async def discard_recovery_jobs(request: Request):
    pending = getattr(request.app.state, "uncompleted_jobs_pending", [])
    count = len(pending)
    journal = getattr(request.app.state, "job_journal", None)
    if journal:
        for r_job in pending:
            jid = r_job.get("job_id")
            if jid:
                journal.log_cancellation(jid, reason="recovery_discarded")
        journal.rotate_session()

    request.app.state.uncompleted_jobs_pending = []
    return {"discarded_count": count, "message": "All unfinished jobs discarded and journal archived"}


@router.post(
    "/{task_id}/cancel",
    summary="Cancel a task",
    status_code=200,
)
async def cancel_task_endpoint(task_id: str, request: Request):
    task_manager = _get_task_manager(request)
    success = task_manager.cancel_task(task_id, reason="operator_cancel")
    if not success:
        raise HTTPException(404, f"Task {task_id!r} not found")
    return {"task_id": task_id, "status": "CANCELLED"}


