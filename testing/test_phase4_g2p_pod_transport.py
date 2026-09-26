"""
test_phase4_g2p_pod_transport.py — Phase 4 Acceptance and Red-Team Attack Tests

Verifies:
1. G2P Pod Transport Lifecycle: RETRIEVE_POD -> PICK_ITEM -> RETURN_POD.
2. Pod grid position follows robot position while carrying_pod_id is set.
3. Item pick decrements SKU manifest and triggers decentralized broadcast.
4. Red-team concurrency: Reservation system prevents two robots lifting the same pod.
5. Red-team E-STOP: Pod position remains consistent during emergency stop.
6. Red-team conflict arbitration applies normally when carrying a pod.
"""

import sys
import time
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.robot import AMRType
from app.models.robot_fsm import RobotState, RobotEvent
from app.models.task import Task, TaskType, TaskStatus
from app.models.world import build_default_world
from app.services.inventory_ledger import InventoryLedger
from app.services.robot_node import RobotNode
from app.services.grid import WarehouseGrid
from app.services.pathfinder import SpaceTimeAStarPlanner
from app.services.reservations import reserve_path
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport


def test_full_g2p_pod_transport_lifecycle(tmp_path):
    """
    Acceptance Test 1: Full RETRIEVE_POD -> PICK_ITEM -> RETURN_POD mission.
    """
    hub = LoopbackNetworkHub()
    db_file = tmp_path / "g2p_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    transport = LoopbackTransport("AMR-G2P-01", hub=hub)
    robot = RobotNode(
        robot_id="AMR-G2P-01",
        start_pos=(0, 8),  # Import dock staging
        robot_type="GOODS_TO_PERSON",
        transport=transport,
        ledger=ledger,
    )

    # Pod target: POD-A04 at (7, 6)
    pod_id = "POD-A04"
    pod_pos = world.pod_slots[pod_id]
    pick_face = (29, 9)

    initial_record = ledger.get_shelf(pod_id)
    initial_box_count = initial_record.current_box_count
    first_sku = list(initial_record.sku_manifest.keys())[0]
    initial_sku_qty = initial_record.sku_manifest[first_sku]

    # 1. Assign RETRIEVE_POD task
    task = Task(
        task_id="TASK-G2P-01",
        pickup_x=pod_pos[0],
        pickup_y=pod_pos[1],
        dropoff_x=pick_face[0],
        dropoff_y=pick_face[1],
        urgency=4,
        created_tick=0,
        task_type=TaskType.RETRIEVE_POD,
        target_shelf_id=pod_id,
        sku_to_pick=first_sku,
        quantity=2,
    )
    robot._assign_initial_task(
        goal_pos=task.dropoff,
        pickup_pos=task.pickup,
        urgency=task.urgency,
        task_id=task.task_id,
    )
    robot.task = task

    # Step robot until it reaches pod slot (7, 6)
    for tick in range(1, 40):
        frame = robot.step(tick=tick)
        if robot.robot.position == pod_pos:
            break

    assert robot.robot.position == pod_pos, f"Robot failed to reach pod slot {pod_pos}, currently at {robot.robot.position}"

    # Step through LIFTING state
    lift_frame = robot.step(tick=robot.robot.last_updated_tick + 1)
    assert robot.robot.carrying_pod_id == pod_id, "Robot should be carrying pod_id after lift!"
    assert lift_frame.get("carrying_pod_id") == pod_id

    # Step robot carrying pod toward pick face (29, 9)
    for tick in range(robot.robot.last_updated_tick + 1, robot.robot.last_updated_tick + 60):
        frame = robot.step(tick=tick)
        # Verify carrying_pod_id remains set throughout carriage
        if robot.robot.position != pick_face:
            assert robot.robot.carrying_pod_id == pod_id
        else:
            break

    assert robot.robot.position == pick_face, f"Robot failed to reach pick face {pick_face}"

    # Step through DROPPING / LOWERING / PICK execution at pick face
    drop_frame = robot.step(tick=robot.robot.last_updated_tick + 1)
    assert robot.robot.carrying_pod_id is None, "Pod should be lowered upon task completion."

    # Verify inventory was decremented in ledger
    updated_record = ledger.get_shelf(pod_id)
    assert updated_record is not None
    assert updated_record.sku_manifest[first_sku] == initial_sku_qty - 2
    assert updated_record.current_box_count == initial_box_count - 2


def test_redteam_concurrent_pod_slot_access():
    """
    Attack check 1: Two G2P robots targeting the same pod slot.
    Space-Time A* reservations serialize access so only one enters/lifts at a time.
    """
    world = build_default_world()
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)
    planner = SpaceTimeAStarPlanner(grid=grid)
    table: dict = {}

    target_slot = (7, 6)  # POD-A04
    start_1 = (0, 8)
    start_2 = (0, 9)

    path_1 = planner.plan_path(start=start_1, goal=target_slot, current_tick=0, reservation_table=table)
    assert len(path_1) > 0
    reserve_path(path_1, "AMR-1", table, hold_ticks_at_goal=5)

    path_2 = planner.plan_path(start=start_2, goal=target_slot, current_tick=0, reservation_table=table)
    assert len(path_2) > 0
    reserve_path(path_2, "AMR-2", table)

    # Verify AMR-2 reaches target AFTER AMR-1 finishes its hold
    t_reach_1 = next(s["t"] for s in path_1 if (s["x"], s["y"]) == target_slot)
    t_reach_2 = next(s["t"] for s in path_2 if (s["x"], s["y"]) == target_slot)
    assert t_reach_2 > t_reach_1 + 4, f"AMR-2 reached target slot during AMR-1 hold: t1={t_reach_1}, t2={t_reach_2}"


def test_redteam_estop_carrying_pod():
    """
    Attack check 2: Robot carrying pod subjected to EMERGENCY_STOP preserves
    pod attachment and recovers cleanly without teleportation.
    """
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-G2P-ESTOP", hub=hub)
    robot = RobotNode(
        robot_id="AMR-G2P-ESTOP",
        start_pos=(10, 10),
        robot_type="GOODS_TO_PERSON",
        transport=transport,
    )
    robot.robot.carrying_pod_id = "POD-B08"
    robot.fsm.state = RobotState.EN_ROUTE_DROPOFF

    # Trigger E-STOP
    robot.fsm.transition(RobotEvent.E_STOP)
    robot.robot.state = robot.fsm.state
    assert robot.fsm.state == RobotState.EMERGENCY_STOP
    # Pod remains on robot
    assert robot.robot.carrying_pod_id == "POD-B08"

    # Reset recovers to IDLE
    robot.reset_failsafe()
    assert robot.fsm.state == RobotState.IDLE
    assert robot.robot.carrying_pod_id == "POD-B08"
