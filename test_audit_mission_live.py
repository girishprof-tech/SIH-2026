"""
test_audit_mission_live.py — Verification of Autonomous Audit Mission Activation and Patrol.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotState
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport


def test_audit_mission_live():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    # Start robot at (6, 4) — nearest checkpoint is (6, 6), exactly 2 steps away
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(6, 4),
        goal_pos=None,
        transport=transport,
    )
    assert node.fsm.state == RobotState.IDLE
    assert node.task is None
    assert node.active_audit_mission is None

    # Step 10 ticks while IDLE to trigger the audit threshold
    entered_auditing = False
    for t in range(1, 11):
        node.step(tick=t)
        if node.fsm.state == RobotState.AUDITING:
            entered_auditing = True

    assert entered_auditing is True, f"Robot did not enter AUDITING after 10 ticks! Current state: {node.fsm.state}"
    assert node.active_audit_mission is not None, "AuditMission was not instantiated!"
    target_cp = node.active_audit_mission.checkpoint
    print(f"Triggered AUDITING patrol to checkpoint {target_cp}")

    # Step forward until robot arrives at checkpoint and returns to IDLE
    returned_to_idle = False
    for t in range(11, 25):
        node.step(tick=t)
        if node.fsm.state == RobotState.IDLE:
            returned_to_idle = True
            break

    assert returned_to_idle is True, f"Robot did not return to IDLE after completing audit! Current state: {node.fsm.state}"
    assert node.robot.position == target_cp, f"Expected final position at {target_cp}, got {node.robot.position}"
    assert node.active_audit_mission is None, "active_audit_mission was not cleared upon completion"
    print("PHASE 6.6 VERIFICATION PASSED: Live RobotNode successfully executed full audit cycle: IDLE -> AUDITING -> checkpoint scan -> IDLE!")


if __name__ == "__main__":
    test_audit_mission_live()
