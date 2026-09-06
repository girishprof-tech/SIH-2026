"""
test_task_id_preservation.py — Verification of Task ID preservation on FAILSAFE_HOLD recovery.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotState
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackTransport, LoopbackNetworkHub


def test_task_id_preservation_watchdog():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(1, 6),
        goal_pos=None,
        transport=transport,
    )
    # Assign custom mission
    custom_task_id = "TASK-EXPEDITE-999"
    node._assign_initial_task(
        goal_pos=(10, 6),
        urgency=5,
        task_id=custom_task_id,
        pickup_pos=(3, 6),
    )
    assert node.task.task_id == custom_task_id

    # Simulate fault triggering FAILSAFE_HOLD mid-mission
    node.fsm.state = RobotState.FAILSAFE_HOLD
    node.robot.state = RobotState.FAILSAFE_HOLD

    # Run 5 ticks to trigger supervisory watchdog recovery
    for t in range(1, 6):
        node.step(tick=t)

    # Robot must have recovered and preserved custom_task_id
    assert node.fsm.state == RobotState.EN_ROUTE_PICKUP, f"Expected EN_ROUTE_PICKUP, got {node.fsm.state}"
    assert node.task is not None, "Task was lost after watchdog recovery"
    assert node.task.task_id == custom_task_id, f"Expected {custom_task_id}, got {node.task.task_id}"
    print("PHASE 6.2 (Watchdog) PASSED: Task ID preserved across auto-recovery!")


def test_task_id_preservation_manual_reset():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-02", hub=hub)
    node = RobotNode(
        robot_id="AMR-02",
        start_pos=(2, 6),
        goal_pos=None,
        transport=transport,
    )
    custom_task_id = "TASK-MANUAL-777"
    node._assign_initial_task(
        goal_pos=(12, 6),
        urgency=4,
        task_id=custom_task_id,
        pickup_pos=(4, 6),
    )

    # Force into FAILSAFE_HOLD
    node.fsm.state = RobotState.FAILSAFE_HOLD
    node.robot.state = RobotState.FAILSAFE_HOLD

    # Operator manually triggers reset_failsafe()
    node.reset_failsafe()

    # Verify parity: robot replanned/reassigned with original task_id
    assert node.fsm.state == RobotState.EN_ROUTE_PICKUP, f"Expected EN_ROUTE_PICKUP, got {node.fsm.state}"
    assert node.task is not None
    assert node.task.task_id == custom_task_id, f"Expected {custom_task_id}, got {node.task.task_id}"
    print("PHASE 6.3 (Manual Reset Parity) PASSED: reset_failsafe() matches watchdog and preserves Task ID!")


if __name__ == "__main__":
    test_task_id_preservation_watchdog()
    test_task_id_preservation_manual_reset()
