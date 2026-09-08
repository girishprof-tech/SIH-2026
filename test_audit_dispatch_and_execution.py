"""
test_audit_dispatch_and_execution.py — Verifies AUDIT robot dispatch, movement, scan, and gate layout.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "archive" / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot import AMRType
from app.models.robot_fsm import RobotState
from app.models.world import build_default_world
from app.services.robot_node import RobotNode
from app.services.task_manager import TaskManager
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from app.models.task import Task, TaskStatus
from app.services.audit_mission import DEFAULT_CHECKPOINTS


def test_gates_layout():
    w = build_default_world()
    assert len(w.pickup_stations) == 9
    assert len(w.dropoff_stations) == 9

    # Check 3 in-gates at x=0 (West wall)
    for y in (8, 9, 10, 13, 14, 15, 18, 19, 20):
        assert (0, y) in w.pickup_stations
        assert w.zone_for(0, y) == "IMPORT_DOCK"

    # Check 3 out-gates at x=29 (East wall)
    for y in (8, 9, 10, 13, 14, 15, 18, 19, 20):
        assert (29, y) in w.dropoff_stations
        assert w.zone_for(29, y) == "EXPORT_DOCK"


def test_audit_dispatch_and_execution():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-08", hub=hub)
    world = build_default_world()

    node = RobotNode(
        robot_id="AMR-08",
        start_pos=(7, 7),  # In aisle near checkpoint (7, 5)
        goal_pos=None,
        obstacles=list(world.static_obstacles),
        robot_type="SCANNING_AUDIT",
        transport=transport,
    )
    assert node.fsm.state == RobotState.IDLE
    assert node.robot_type == "SCANNING_AUDIT"

    # Verify telemetry frame includes robot_type
    frame = node._build_telemetry_frame(0, "IDLE", None)
    assert frame["robot_type"] == "SCANNING_AUDIT"

    # Dispatch audit task
    checkpoint = (7, 5)
    assert checkpoint in DEFAULT_CHECKPOINTS
    assert not world.is_static_blocked(checkpoint[0], checkpoint[1])

    task_mgr = TaskManager()
    audit_task = Task(
        task_id="AUDIT-AMR-08-001",
        pickup_x=node.robot.position[0],
        pickup_y=node.robot.position[1],
        dropoff_x=checkpoint[0],
        dropoff_y=checkpoint[1],
        urgency=3,
        created_tick=0,
        status=TaskStatus.ASSIGNED,
        assigned_robot_id="AMR-08",
    )

    task_mgr.dispatch_to_fleet(
        task=audit_task,
        transport_sender=lambda recipient, envelope: hub.deliver(recipient, envelope),
        target_robot_id="AMR-08",
    )

    # Step 1: Drain inbox and accept task
    node.step(tick=1)
    assert node.fsm.state == RobotState.AUDITING, f"Expected AUDITING, got {node.fsm.state}"
    assert node.active_audit_mission is not None
    assert len(node.robot.path) > 0, "Audit path was not planned!"

    # Step through ticks until robot reaches (7, 5) and completes scan
    completed = False
    for t in range(2, 20):
        node.step(tick=t)
        if node.fsm.state == RobotState.IDLE and node.active_audit_mission is None:
            completed = True
            break

    assert completed is True, f"Robot did not complete audit mission! Current state: {node.fsm.state}"
    assert node.robot.position == checkpoint
    assert "AUDIT-AMR-08-001" in node.completed_task_ids


if __name__ == "__main__":
    test_gates_layout()
    test_audit_dispatch_and_execution()
    print("ALL AUDIT AND GATE TESTS PASSED!")
