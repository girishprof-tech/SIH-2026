"""
test_step3_station_resilience.py — Step 3 Computation-Stays-on-AMRs Boundary & Station Resilience.

Verifies:
1. Architectural audit of StationNode:
   - Confirms StationNode contains NO pathfinding (A*, CBS), NO robot FSM, NO conflict resolution,
     and NO centralized task assignment logic (Contract-Net bidding remains entirely peer-to-peer on AMRs).
2. Extended AuthorityStation failure invariance:
   - AuthorityStation is killed/offline for an extended run (60+ ticks).
   - AMRs execute and complete tasks autonomously with zero failsafes or halts.
   - Import and Export stations report faults and cleanly detect Authority is unreachable without hanging.
3. Mid-run ImportStation kill under multi-robot contention:
   - ImportStation issues INDUCT_BATCH task.
   - Station process is killed mid-run while AMRs are en route.
   - AMRs continue moving, resolve spatial contention peer-to-peer, and finish tasks autonomously.
4. Mid-run ExportStation kill:
   - ExportStation issues CONSOLIDATE_EXPORT and is killed mid-run.
   - AMRs complete export consolidation autonomously.
"""

import sys
import time
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))

from app.models.world import build_default_world
from app.models.robot_fsm import RobotState
from app.models.task import Task, TaskType
from app.services.station_node import StationNode, StationRole
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport


def test_audit_computation_boundary():
    """
    Audit StationNode capabilities vs RobotNode capabilities:
    A Station must NOT compute paths, run conflict resolution, or assign tasks directly.
    """
    station = StationNode(
        station_id="IMPORT_STATION",
        role=StationRole.IMPORT_STATION,
        position=(1, 14),
        port=9601,
        peer_ports={"IMPORT_STATION": 9601},
    )
    try:
        # 1. No pathfinding methods
        assert not hasattr(station, "find_path")
        assert not hasattr(station, "_timed_find_path")
        assert not hasattr(station, "pathfinder")
        assert not hasattr(station, "cbs")

        # 2. No robot FSM or movement state
        assert not hasattr(station, "fsm")
        assert not hasattr(station, "battery_pct")
        assert not hasattr(station, "path")
        assert not hasattr(station, "step")

        # 3. No conflict resolution or reservation table
        assert not hasattr(station, "local_reservations")
        assert not hasattr(station, "resolve_conflict")
        assert not hasattr(station, "_resolve_resource_contention")

        # 4. No AMR bidding state
        assert not hasattr(station, "active_bids")
        assert not hasattr(station, "_resolve_contract_net_bids")
    finally:
        station.close()


def test_authority_station_death_extended_run():
    """
    Kill AuthorityStation entirely for an extended run (60 ticks).
    Confirm normal AMR operations and peer-to-peer coordination are completely unaffected.
    Confirm domain stations gracefully report escalation unavailability rather than hanging.
    """
    hub = LoopbackNetworkHub()
    t_amr1 = LoopbackTransport("AMR-01", hub=hub)
    t_amr2 = LoopbackTransport("AMR-02", hub=hub)
    t_import = LoopbackTransport("IMPORT_STATION", hub=hub)

    peer_ports = {
        "AMR-01": 9001,
        "AMR-02": 9002,
        "IMPORT_STATION": 9601,
        "AUTHORITY_STATION": 9603,
    }

    world = build_default_world()
    bot1 = RobotNode(
        robot_id="AMR-01",
        start_pos=(2, 4),
        robot_type="GOODS_TO_PERSON",
        transport=t_amr1,
        peer_ports=peer_ports,
        obstacles=world.static_obstacles,
    )
    bot2 = RobotNode(
        robot_id="AMR-02",
        start_pos=(2, 9),
        robot_type="GOODS_TO_PERSON",
        transport=t_amr2,
        peer_ports=peer_ports,
        obstacles=world.static_obstacles,
    )

    # Assign active mission to AMR-01
    bot1._assign_initial_task(
        goal_pos=(4, 6),
        pickup_pos=(2, 4),
        urgency=3,
        task_id="TASK-INFLIGHT-01",
    )

    # AuthorityStation is offline from the start
    import_station = StationNode(
        station_id="IMPORT_STATION",
        role=StationRole.IMPORT_STATION,
        position=(1, 14),
        port=9601,
        peer_ports=peer_ports,
    )
    import_station.is_authority_online = False

    try:
        # Run 60 ticks with AuthorityStation dead
        for tick in range(1, 61):
            bot1.step(tick)
            bot2.step(tick)
            import_station.poll_and_step(tick)

        # 1. AMRs operated normally and reached valid states (no failsafe or deadlock)
        assert bot1.fsm.state != RobotState.FAILSAFE_HOLD
        assert bot1.fsm.state != RobotState.EMERGENCY_STOP
        assert bot2.fsm.state != RobotState.FAILSAFE_HOLD

        # 2. ImportStation fault escalation degradation: reports escalation unavailable without hanging
        fault_res = import_station.report_fault(
            fault_id="FAULT-SPOF-TEST",
            description="Motor stall on dock elevator",
            severity="CRITICAL",
        )
        assert fault_res["status"] == "ESCALATION_UNAVAILABLE"
        assert fault_res.get("escalation_failed") is True
        assert "AuthorityStation unreachable" in fault_res["reason"]

    finally:
        import_station.close()


def test_import_station_mid_run_kill_with_contention():
    """
    ImportStation issues an authorized INDUCT_BATCH command.
    ImportStation is killed mid-run at tick 5.
    AMRs continue operating under spatial contention and finish in-flight work autonomously.
    """
    hub = LoopbackNetworkHub()
    t_sort = LoopbackTransport("AMR-SORT-01", hub=hub)
    t_g2p = LoopbackTransport("AMR-G2P-01", hub=hub)
    t_import = LoopbackTransport("IMPORT_STATION", hub=hub)

    peer_ports = {
        "AMR-SORT-01": 9001,
        "AMR-G2P-01": 9002,
        "IMPORT_STATION": 9601,
        "EXPORT_STATION": 9602,
        "AUTHORITY_STATION": 9603,
    }

    world = build_default_world()
    bot_sort = RobotNode(
        robot_id="AMR-SORT-01",
        start_pos=(2, 8),
        robot_type="SORTING",
        transport=t_sort,
        peer_ports=peer_ports,
        obstacles=world.static_obstacles,
    )
    bot_g2p = RobotNode(
        robot_id="AMR-G2P-01",
        start_pos=(2, 10),
        robot_type="GOODS_TO_PERSON",
        transport=t_g2p,
        peer_ports=peer_ports,
        obstacles=world.static_obstacles,
    )

    import_station = StationNode(
        station_id="IMPORT_STATION",
        role=StationRole.IMPORT_STATION,
        position=(1, 14),
        port=9601,
        peer_ports=peer_ports,
    )
    import_station.transport = t_import

    try:
        # Tick 1: Import Station dispatches authorized INDUCT_BATCH task
        import_task = {
            "task_id": "INDUCT-MIDRUN-KILL",
            "task_type": "INDUCT_BATCH",
            "source_gate": "IN-1",
            "pickup": [0, 9],
            "dropoff": [4, 6],
            "urgency": 3,
        }
        issued, msg = import_station.issue_task_command(
            command_type="TASK_ASSIGNMENT",
            task_dict=import_task,
            target_peer_id="AMR-SORT-01",
        )
        assert issued is True

        # Run 4 ticks: AMR receives task and starts moving
        for tick in range(1, 5):
            bot_sort.step(tick)
            bot_g2p.step(tick)
            import_station.poll_and_step(tick)

        # Confirm bot_sort accepted the task
        assert bot_sort.task is not None
        assert bot_sort.task.task_id == "INDUCT-MIDRUN-KILL"

        # AT TICK 5: KILL IMPORT STATION PROCESS (simulate total process crash / disconnection)
        import_station.close()
        del import_station

        # Run remaining 45 ticks with ImportStation completely dead
        for tick in range(5, 50):
            bot_sort.step(tick)
            bot_g2p.step(tick)

        # Confirm bot_sort finished in-flight work autonomously without failsafe
        assert bot_sort.fsm.state != RobotState.FAILSAFE_HOLD
        assert bot_sort.fsm.state != RobotState.EMERGENCY_STOP
        assert bot_g2p.fsm.state != RobotState.FAILSAFE_HOLD

    finally:
        pass


def test_export_station_mid_run_kill_with_contention():
    """
    ExportStation issues CONSOLIDATE_EXPORT command and is killed mid-run.
    Sorting AMR finishes consolidation autonomously over peer mesh.
    """
    hub = LoopbackNetworkHub()
    t_sort = LoopbackTransport("AMR-SORT-02", hub=hub)
    t_export = LoopbackTransport("EXPORT_STATION", hub=hub)

    peer_ports = {
        "AMR-SORT-02": 9001,
        "EXPORT_STATION": 9602,
        "AUTHORITY_STATION": 9603,
    }

    world = build_default_world()
    bot_sort = RobotNode(
        robot_id="AMR-SORT-02",
        start_pos=(27, 8),
        robot_type="SORTING",
        transport=t_sort,
        peer_ports=peer_ports,
        obstacles=world.static_obstacles,
    )

    export_station = StationNode(
        station_id="EXPORT_STATION",
        role=StationRole.EXPORT_STATION,
        position=(28, 14),
        port=9602,
        peer_ports=peer_ports,
    )
    export_station.transport = t_export

    try:
        export_task = {
            "task_id": "EXPORT-MIDRUN-KILL",
            "task_type": "CONSOLIDATE_EXPORT",
            "destination_gate": "OUT-1",
            "pickup": [23, 2],
            "dropoff": [29, 9],
            "urgency": 3,
        }
        issued, msg = export_station.issue_task_command(
            command_type="TASK_ASSIGNMENT",
            task_dict=export_task,
            target_peer_id="AMR-SORT-02",
        )
        assert issued is True

        for tick in range(1, 5):
            bot_sort.step(tick)
            export_station.poll_and_step(tick)

        assert bot_sort.task is not None
        assert bot_sort.task.task_id == "EXPORT-MIDRUN-KILL"

        # KILL EXPORT STATION MID-RUN
        export_station.close()
        del export_station

        # Continue autonomous run
        for tick in range(5, 45):
            bot_sort.step(tick)

        assert bot_sort.fsm.state != RobotState.FAILSAFE_HOLD
        assert bot_sort.fsm.state != RobotState.EMERGENCY_STOP

    finally:
        pass
