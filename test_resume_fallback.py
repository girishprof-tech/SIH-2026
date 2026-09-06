"""
test_resume_fallback.py — Verification of CONFLICT_NEGOTIATING fallback to FAILSAFE_HOLD.
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


def test_resume_fallback_failsafe():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(1, 6),
        goal_pos=None,
        transport=transport,
    )
    # Put robot into CONFLICT_NEGOTIATING
    node.fsm.state = RobotState.CONFLICT_NEGOTIATING
    node.robot.state = RobotState.CONFLICT_NEGOTIATING

    # Manually clear pre_conflict_activity to None
    node.pre_conflict_activity = None

    # Give it a path step to execute so movement/resume code path triggers
    node.robot.path = [{"x": 1, "y": 6}, {"x": 2, "y": 6}]

    # Execute step()
    node.step(tick=1)

    # Must transition to FAILSAFE_HOLD, NOT default to EN_ROUTE_PICKUP
    assert node.fsm.state == RobotState.FAILSAFE_HOLD, f"Expected FAILSAFE_HOLD, got {node.fsm.state}"
    assert node.robot.state == RobotState.FAILSAFE_HOLD
    print("PHASE 6.4 VERIFICATION PASSED: Unspecified pre_conflict_activity safely falls back to FAILSAFE_HOLD!")


if __name__ == "__main__":
    test_resume_fallback_failsafe()
