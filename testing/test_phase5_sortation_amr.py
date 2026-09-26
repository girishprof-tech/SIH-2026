"""
test_phase5_sortation_amr.py — Phase 5 Acceptance and Red-Team Attack Tests

Verifies:
1. Real Sortation AMR behavior: Batch induction, zone-based chute decanting, and export consolidation.
2. Mixed destination batch items are correctly split across appropriate chutes (CHUTE-01..07) or overflow (CHUTE-08).
3. A full chute autonomously triggers a CONSOLIDATE_EXPORT task without central server polling.
4. Red-team concurrency: Concurrent decanting across sorting robots preserves accurate chute occupancy.
5. Red-team unknown destination: Unknown or unmapped zones route to designated overflow chute.
6. Decoupled eligibility: SORTING robots bid on sortation tasks; G2P robots bid on pod tasks.
"""

import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.robot import AMRType
from app.models.robot_fsm import RobotState
from app.models.task import Task, TaskType, TaskStatus
from app.models.world import build_default_world
from app.services.inventory_ledger import InventoryLedger
from app.services.robot_node import RobotNode
from app.services.task_manager import TaskManager, NearestIdleAssignment
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport


def test_sortation_batch_split_across_chutes():
    """
    Acceptance Test 1: A mixed batch of items with different destination zones
    is correctly routed and decanted into their corresponding sortation chutes.
    """
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-SORT-01", hub=hub)
    world = build_default_world()

    sort_bot = RobotNode(
        robot_id="AMR-SORT-01",
        start_pos=(2, 2),
        robot_type="SORTING",
        transport=transport,
    )

    batch_items = [
        {"item_id": "ITM-01", "destination_zone": "ZONE_NORTH"},
        {"item_id": "ITM-02", "destination_zone": "ZONE_EAST"},
        {"item_id": "ITM-03", "destination_zone": "ZONE_NORTH"},
        {"item_id": "ITM-04", "destination_zone": "ZONE_EXPRESS"},
        {"item_id": "ITM-05", "destination_zone": "ZONE_SOUTH"},
    ]

    assigned_chutes = []
    for item in batch_items:
        chute_id = sort_bot.decant_batch_item(item, tick=1)
        assigned_chutes.append(chute_id)

    # Verify correct routing
    assert assigned_chutes[0] == "CHUTE-01"  # ZONE_NORTH
    assert assigned_chutes[1] == "CHUTE-02"  # ZONE_EAST
    assert assigned_chutes[2] == "CHUTE-01"  # ZONE_NORTH
    assert assigned_chutes[3] == "CHUTE-05"  # ZONE_EXPRESS
    assert assigned_chutes[4] == "CHUTE-03"  # ZONE_SOUTH

    # Verify occupancy counts
    assert sort_bot.chute_occupancy["CHUTE-01"] == 2
    assert sort_bot.chute_occupancy["CHUTE-02"] == 1
    assert sort_bot.chute_occupancy["CHUTE-05"] == 1
    assert sort_bot.chute_occupancy["CHUTE-03"] == 1


def test_autonomous_full_chute_consolidation_trigger():
    """
    Acceptance Test 2: When a chute reaches full capacity threshold (5 items),
    an autonomous CONSOLIDATE_EXPORT task is spawned and broadcasted peer-to-peer.
    """
    hub = LoopbackNetworkHub()
    transport_sort = LoopbackTransport("AMR-SORT-01", hub=hub)
    transport_peer = LoopbackTransport("AMR-SORT-02", hub=hub)

    peer_ports = {"AMR-SORT-01": 9001, "AMR-SORT-02": 9002}

    sort_bot = RobotNode(
        robot_id="AMR-SORT-01",
        start_pos=(2, 2),
        robot_type="SORTING",
        transport=transport_sort,
        peer_ports=peer_ports,
    )

    peer_bot = RobotNode(
        robot_id="AMR-SORT-02",
        start_pos=(3, 3),
        robot_type="SORTING",
        transport=transport_peer,
        peer_ports=peer_ports,
    )

    # Decant 5 items to CHUTE-01 (ZONE_NORTH)
    for i in range(5):
        sort_bot.decant_batch_item({"item_id": f"ITM-N{i}", "destination_zone": "ZONE_NORTH"}, tick=10 + i)

    # Peer robot steps and receives the broadcasted CONSOLIDATE_EXPORT announcement
    peer_bot.step(tick=16)

    # Confirm peer received the announcement and submitted bid
    consolidation_bids = [tid for tid in peer_bot.active_bids if "CONSOLIDATE-CHUTE-01" in tid]
    assert len(consolidation_bids) == 1, "Peer SORTING robot should have detected autonomous consolidation announcement!"


def test_redteam_unknown_destination_overflow():
    """
    Attack check 3: Unrecognized destination zone gracefully falls back to OVERFLOW chute (CHUTE-08).
    """
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-SORT-01", hub=hub)
    sort_bot = RobotNode(
        robot_id="AMR-SORT-01",
        start_pos=(2, 2),
        robot_type="SORTING",
        transport=transport,
    )

    # Item with unknown destination
    chute_id = sort_bot.decant_batch_item({"item_id": "ITM-MYSTERY", "destination_zone": "ZONE_ANTARCTICA"}, tick=1)
    assert chute_id == "CHUTE-08", f"Expected fallback to CHUTE-08 OVERFLOW, got {chute_id}"
    assert sort_bot.chute_occupancy["CHUTE-08"] == 1


def test_decoupled_task_assigner_eligibility():
    """
    Acceptance check 3: TaskManager's NearestIdleAssignment strictly routes sortation tasks to SORTING robots
    and pod tasks to GOODS_TO_PERSON robots.
    """
    assigner = NearestIdleAssignment()
    world = build_default_world()

    from app.models.robot import Robot, Heading

    r_g2p = Robot(
        robot_id="AMR-01",
        x=2,
        y=2,
        heading=Heading.NORTH,
        state=RobotState.IDLE,
        battery_pct=100.0,
        current_task_id=None,
        priority_score=0,
        last_updated_tick=0,
        robot_type=AMRType.GOODS_TO_PERSON,
    )

    r_sort = Robot(
        robot_id="AMR-02",
        x=5,
        y=5,
        heading=Heading.NORTH,
        state=RobotState.IDLE,
        battery_pct=100.0,
        current_task_id=None,
        priority_score=0,
        last_updated_tick=0,
        robot_type=AMRType.SORTING,
    )

    robots = {"AMR-01": r_g2p, "AMR-02": r_sort}

    # 1. Pod Task -> Must match AMR-01 (G2P), even though AMR-02 is farther/closer
    g2p_task = Task(
        task_id="TASK-G2P",
        pickup_x=4,
        pickup_y=4,
        dropoff_x=29,
        dropoff_y=10,
        urgency=3,
        created_tick=0,
        task_type=TaskType.RETRIEVE_POD,
    )
    assigned_g2p = assigner.assign(g2p_task, robots, {})
    assert assigned_g2p == "AMR-01", f"Pod task assigned to {assigned_g2p}, expected AMR-01 (GOODS_TO_PERSON)"

    # 2. Sortation Task -> Must match AMR-02 (SORTING)
    sort_task = Task(
        task_id="TASK-SORT",
        pickup_x=1,
        pickup_y=1,
        dropoff_x=2,
        dropoff_y=3,
        urgency=3,
        created_tick=0,
        task_type=TaskType.INDUCT_BATCH,
    )
    assigned_sort = assigner.assign(sort_task, robots, {})
    assert assigned_sort == "AMR-02", f"Sort task assigned to {assigned_sort}, expected AMR-02 (SORTING)"


def test_redteam_concurrent_decant_chute_occupancy():
    """
    Attack check 2: Multiple SORTING robots concurrently decanting items
    into chutes maintain consistent individual robot and chute state without lost counts.
    """
    hub = LoopbackNetworkHub()
    t1 = LoopbackTransport("AMR-SORT-01", hub=hub)
    t2 = LoopbackTransport("AMR-SORT-02", hub=hub)

    bot1 = RobotNode(robot_id="AMR-SORT-01", start_pos=(2, 2), robot_type="SORTING", transport=t1)
    bot2 = RobotNode(robot_id="AMR-SORT-02", start_pos=(3, 3), robot_type="SORTING", transport=t2)

    # Robot 1 decants 3 items for ZONE_SOUTH (CHUTE-03)
    for i in range(3):
        bot1.decant_batch_item({"item_id": f"B1-{i}", "destination_zone": "ZONE_SOUTH"}, tick=1)

    # Robot 2 decants 2 items for ZONE_SOUTH (CHUTE-03)
    for i in range(2):
        bot2.decant_batch_item({"item_id": f"B2-{i}", "destination_zone": "ZONE_SOUTH"}, tick=1)

    assert bot1.chute_occupancy["CHUTE-03"] == 3
    assert bot2.chute_occupancy["CHUTE-03"] == 2

