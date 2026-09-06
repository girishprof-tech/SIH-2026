"""
test_task_dispatch_e2e.py — End-to-End Real Task Assignment Dispatch Verification.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotState
from app.models.task import TaskStatus
from app.services.robot_node import RobotNode
from app.services.task_manager import TaskManager, build_task_assignment_envelope
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport

def test_task_dispatch_e2e():
    # 1. Spin up Loopback Network and RobotNode in IDLE state
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(1, 6),
        goal_pos=None,  # No initial mission -> Starts in IDLE
        transport=transport,
    )
    assert node.fsm.state == RobotState.IDLE, f"Expected IDLE, got {node.fsm.state}"
    assert node.task is None

    # 2. Create pending mission via TaskManager
    task_mgr = TaskManager()
    task = task_mgr.create_task(
        pickup_x=3,
        pickup_y=6,
        dropoff_x=12,
        dropoff_y=6,
        urgency=4,
        current_tick=0,
    )
    assert task.status == TaskStatus.PENDING

    # 3. Exercise real TaskManager.dispatch_to_fleet code path
    assigned = task_mgr.dispatch_to_fleet(
        task=task,
        transport_sender=lambda recipient, envelope: hub.deliver(recipient, envelope),
        target_robot_id="AMR-01",
    )
    assert assigned == "AMR-01"
    assert task.status == TaskStatus.ASSIGNED
    assert task.assigned_robot_id == "AMR-01"

    # 4. Robot executes autonomous step(1), processing the dispatch envelope
    node.step(tick=1)

    # 5. Assert authoritative transition: IDLE -> ASSIGNED -> EN_ROUTE_PICKUP
    assert node.fsm.state == RobotState.EN_ROUTE_PICKUP, f"Expected EN_ROUTE_PICKUP, got {node.fsm.state}"
    assert node.task is not None, "Task was not assigned to robot!"
    assert node.task.task_id == task.task_id, f"Task ID mismatch: {node.task.task_id} vs {task.task_id}"
    assert node.task.pickup == (3, 6)
    assert node.task.dropoff == (12, 6)
    assert len(node.robot.path) > 0, "Robot path was not planned to pickup!"
    print(f"-> Verified: Task {task.task_id} dispatched and received. Robot state: {node.fsm.state.value}, Path length: {len(node.robot.path)}")

if __name__ == "__main__":
    test_task_dispatch_e2e()
    print("ALL TESTS PASSED: test_task_dispatch_e2e.py")
