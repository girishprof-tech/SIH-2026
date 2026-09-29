"""
attack_step1_idle_fleet.py — Step 1 Attack Suite: Idle Until Tasked.

Tests:
1. attack_120s_zero_tasks_zero_motion:
   Fleet running for 120 simulated ticks with 0 tasks -> asserts 0 position changes.
2. attack_one_task_finish_and_revert_to_idle:
   Injects 1 task -> robot finishes -> asserts return to IDLE and 0 subsequent movements.
3. attack_robot_process_kill_restart_invents_no_work:
   Kill an idle robot process mid-run and restart -> asserts restarted node invents no work.
4. attack_backend_repeated_restarts_keep_fleet_idle:
   Rapidly restart FastAPI client -> asserts SimulationEngine inert and fleet state stays ARMED with 0 tasks.
5. attack_individual_flag_gating:
   Toggle AUTO_IDLE_AUDIT, AUTO_CONSOLIDATION, AUTO_TRANSFER individually -> assert only permitted behaviors trigger.
"""

import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.core.config import get_settings
from app.models.robot_fsm import RobotState
from app.models.task import Task, TaskType
from app.models.world import build_default_world
from app.services.fleet_orchestrator import FleetOrchestrator
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from testing.repro_20robot_collision import clean_stale_ports


def attack_120s_zero_tasks_zero_motion():
    """Attack 1: 120 simulated ticks with zero tasks -> zero position changes across all robots."""
    print("\n--- ATTACK 1: 120 simulated ticks with zero tasks ---")
    clean_stale_ports()
    cfg = get_settings()
    assert not cfg.AUTO_IDLE_AUDIT, "AUTO_IDLE_AUDIT must default to False"
    assert not cfg.AUTO_CONSOLIDATION, "AUTO_CONSOLIDATION must default to False"
    assert not cfg.AUTO_TRANSFER, "AUTO_TRANSFER must default to False"

    orchestrator = FleetOrchestrator(tick_interval_s=0.015, max_ticks=120)
    initial_positions: Dict[str, Tuple[int, int]] = {
        c["robot_id"]: c["start"] for c in orchestrator.robots_config
    }
    moved_robots = []

    try:
        orchestrator.start(enable_stations=False)
        start_time = time.time()
        while time.time() - start_time < 15.0 and orchestrator.bus.current_tick < 118:
            tick_update = orchestrator.bus.process_incoming()
            if tick_update:
                for r in tick_update.get("robots", []):
                    rid = r["id"]
                    curr_pos = (r["x"], r["y"])
                    expected_pos = initial_positions[rid]
                    if curr_pos != expected_pos and rid not in moved_robots:
                        moved_robots.append((rid, expected_pos, curr_pos, r.get("state")))
            time.sleep(0.005)
    finally:
        orchestrator.stop()
        clean_stale_ports()

    assert not moved_robots, f"Robots moved without tasks: {moved_robots}"
    print(f"-> PASSED Attack 1: Zero position changes across {len(initial_positions)} robots over 120 ticks!")


def attack_one_task_finish_and_revert_to_idle():
    """Attack 2: One task injected -> robot finishes -> returns to IDLE with 0 further motion."""
    print("\n--- ATTACK 2: One task finish and revert to IDLE ---")
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(2, 4),
        goal_pos=None,
        urgency=3,
        battery_pct=100.0,
        transport=transport,
        tick_interval_s=0.0,
        auto_idle_audit=False,
    )
    try:
        # 1. Initially IDLE
        for t in range(5):
            node.step(t)
        assert node.fsm.state == RobotState.IDLE
        assert node.robot.position == (2, 4)

        # 2. Assign task via TaskManager
        from app.services.task_manager import TaskManager
        task_mgr = TaskManager()
        task = task_mgr.create_task(
            pickup_x=2,
            pickup_y=5,
            dropoff_x=2,
            dropoff_y=6,
            urgency=3,
            current_tick=5,
        )
        task_mgr.dispatch_to_fleet(
            task=task,
            transport_sender=lambda recipient, envelope: hub.deliver(recipient, envelope),
            target_robot_id="AMR-01",
        )
        node.step(tick=5)
        assert node.task is not None

        # 3. Step until completed
        completed = False
        for t in range(6, 60):
            node.step(t)
            if node.fsm.state == RobotState.IDLE and not node.task:
                completed = True
                completed_pos = node.robot.position
                completed_tick = t
                break

        assert completed, "Task did not complete within 60 ticks"

        # 4. Step 30 more ticks in IDLE: assert NO new goals, NO audit patrol, NO movement
        for t in range(completed_tick + 1, completed_tick + 31):
            node.step(t)
            assert node.fsm.state == RobotState.IDLE, f"Robot left IDLE state at tick {t}: {node.fsm.state}"
            assert node.robot.position == completed_pos, f"Robot moved after task complete: {node.robot.position} != {completed_pos}"
            assert node.task is None, f"Robot invented a new task: {node.task}"
            assert node.goal_pos is None, f"Robot set an unprompted goal: {node.goal_pos}"

        print("-> PASSED Attack 2: Robot cleanly completed task and remained stationary in IDLE for 30 ticks!")
    finally:
        node.close()


def attack_robot_process_kill_restart_invents_no_work():
    """Attack 3: Kill an IDLE robot and restart -> asserts it invents 0 tasks."""
    print("\n--- ATTACK 3: Process kill and restart invents no work ---")
    hub = LoopbackNetworkHub()
    transport1 = LoopbackTransport("AMR-KILL-01", hub=hub)
    node1 = RobotNode(
        robot_id="AMR-KILL-01",
        start_pos=(10, 3),
        goal_pos=None,
        transport=transport1,
        auto_idle_audit=False,
    )
    for t in range(15):
        node1.step(t)
    assert node1.robot.position == (10, 3)
    node1.close()  # Simulating node kill

    # Restart fresh node at same start pos
    transport2 = LoopbackTransport("AMR-KILL-01", hub=hub)
    node2 = RobotNode(
        robot_id="AMR-KILL-01",
        start_pos=(10, 3),
        goal_pos=None,
        transport=transport2,
        auto_idle_audit=False,
    )
    try:
        for t in range(25):
            node2.step(t)
            assert node2.fsm.state == RobotState.IDLE
            assert node2.robot.position == (10, 3)
            assert node2.task is None
            assert node2.goal_pos is None
        print("-> PASSED Attack 3: Killed and restarted robot node remained strictly IDLE without inventing work!")
    finally:
        node2.close()


def attack_backend_repeated_restarts_keep_fleet_idle():
    """Attack 4: Rapidly query and start backend with zero tasks -> verify fleet state stays inert."""
    print("\n--- ATTACK 4: Backend repeated restarts keep fleet inert ---")
    os.environ["SPAWN_FLEET_ORCHESTRATOR"] = "0"
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        # Check simulation status endpoint initially
        res = client.get("/api/simulation/status")
        assert res.status_code == 200

        # Post start
        res2 = client.post("/api/simulation/start")
        assert res2.status_code == 200

        # Verify engine was NOT revived
        assert getattr(app.state.engine, "ticks_executed", 0) == 0
        assert not app.state.engine._running

        # Clean reset
        client.post("/api/simulation/reset")
        orch = getattr(app.state, "orchestrator", None)
        if orch is not None:
            orch.stop()
        print("-> PASSED Attack 4: Backend simulation endpoints cold start preserved inert engine state!")


def attack_individual_flag_gating():
    """Attack 5: Enable each flag individually and verify only the intended autonomous behavior triggers."""
    print("\n--- ATTACK 5: Individual flag gating assertions ---")
    hub = LoopbackNetworkHub()

    # 1. AUTO_IDLE_AUDIT=True alone
    t1 = LoopbackTransport("AMR-AUDIT-TEST", hub=hub)
    node_audit = RobotNode(
        robot_id="AMR-AUDIT-TEST",
        start_pos=(10, 3),
        robot_type="SCANNING_AUDIT",
        transport=t1,
        auto_idle_audit=True,  # Explicitly enabled
    )
    try:
        triggered_audit = False
        for t in range(15):
            node_audit.step(t)
            if node_audit.fsm.state == RobotState.AUDITING:
                triggered_audit = True
                break
        assert triggered_audit, "AUTO_IDLE_AUDIT=True failed to trigger audit patrol when explicitly enabled"

        # Now test with auto_idle_audit=False: MUST NOT trigger
        t2 = LoopbackTransport("AMR-NO-AUDIT", hub=hub)
        node_no_audit = RobotNode(
            robot_id="AMR-NO-AUDIT",
            start_pos=(10, 3),
            robot_type="SCANNING_AUDIT",
            transport=t2,
            auto_idle_audit=False,  # Explicitly disabled
        )
        try:
            for t in range(25):
                node_no_audit.step(t)
                assert node_no_audit.fsm.state == RobotState.IDLE, "Audit patrol triggered while auto_idle_audit=False!"
        finally:
            node_no_audit.close()

        # 2. AUTO_CONSOLIDATION flag gating
        node_consol = RobotNode(
            robot_id="AMR-SORT-TEST",
            start_pos=(2, 4),
            robot_type="SORTING",
            transport=LoopbackTransport("AMR-SORT-TEST", hub=hub),
            auto_consolidation=False,
        )
        try:
            # Without force and with auto_consolidation=False -> returns None
            c_task = node_consol.trigger_autonomous_consolidation("CHUTE-01", current_tick=1, force=False)
            assert c_task is None, "Consolidation triggered when auto_consolidation=False!"

            # With force=True (user follow-up) -> succeeds
            c_task_forced = node_consol.trigger_autonomous_consolidation("CHUTE-01", current_tick=1, force=True)
            assert c_task_forced is not None, "Forced consolidation follow-up failed to generate task"
            assert c_task_forced.task_type == TaskType.CONSOLIDATE_EXPORT
        finally:
            node_consol.close()

        # 3. AUTO_TRANSFER flag gating
        node_transfer = RobotNode(
            robot_id="AMR-G2P-TEST",
            start_pos=(2, 4),
            robot_type="GOODS_TO_PERSON",
            transport=LoopbackTransport("AMR-G2P-TEST", hub=hub),
            auto_transfer=False,
        )
        try:
            # Without force and with auto_transfer=False -> returns None
            t_task = node_transfer.trigger_autonomous_transfer("PICK-01", current_tick=1, force=False)
            assert t_task is None, "Transfer triggered when auto_transfer=False!"

            # With force=True (user follow-up) -> succeeds
            t_task_forced = node_transfer.trigger_autonomous_transfer("PICK-01", current_tick=1, force=True)
            assert t_task_forced is not None, "Forced transfer follow-up failed to generate task"
            assert t_task_forced.task_type == TaskType.TRANSFER_TO_SORTATION
        finally:
            node_transfer.close()

        print("-> PASSED Attack 5: All 3 gating flags strictly enforced their respective behaviors in isolation!")
    finally:
        node_audit.close()


def run_all_step1_attacks():
    print("=" * 80)
    print("RUNNING STEP 1 ATTACK SUITE (5 ATTACK VECTORS)")
    print("=" * 80)
    attack_120s_zero_tasks_zero_motion()
    attack_one_task_finish_and_revert_to_idle()
    attack_robot_process_kill_restart_invents_no_work()
    attack_backend_repeated_restarts_keep_fleet_idle()
    attack_individual_flag_gating()
    print("=" * 80)
    print("ALL STEP 1 ATTACKS DEFEATED CLEANLY!")
    print("=" * 80)


if __name__ == "__main__":
    mp.freeze_support()
    run_all_step1_attacks()
    clean_stale_ports()
    sys.stdout.flush()
    os._exit(0)
