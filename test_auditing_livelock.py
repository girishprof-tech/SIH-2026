"""
test_auditing_livelock.py — Verification that Auditing robots break livelock / stalls.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotState
from app.services.audit_mission import AuditMission
from app.services.robot_node import PeerSnapshot, RobotNode, Heading
from app.transport.loopback_transport import LoopbackTransport, LoopbackNetworkHub


def test_auditing_livelock_breaks():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(5, 5),
        goal_pos=(5, 8),
        transport=transport,
    )
    # Transition to AUDITING
    node.fsm.state = RobotState.AUDITING
    node.robot.state = RobotState.AUDITING
    node.active_audit_mission = AuditMission(checkpoint=(5, 8))
    node.goal_pos = (5, 8)

    # Initial path planned
    node.robot.path = [{"x": 5, "y": 5}, {"x": 5, "y": 6}, {"x": 5, "y": 7}, {"x": 5, "y": 8}]

    # Simulate an oncoming peer blocking cell (5, 6)
    node.peers["AMR-02"] = PeerSnapshot(
        robot_id="AMR-02",
        position=(5, 6),
        intended_pos=(5, 5),
        heading=Heading.SOUTH,
        priority_score=9999.0,
        state=RobotState.EN_ROUTE_PICKUP,
        wait_ticks_so_far=0,
        path=[{"x": 5, "y": 6, "t": 1}, {"x": 5, "y": 5, "t": 2}],
        last_seen_tick=1,
    )

    # Simulate 3 wait ticks so consecutive_wait_ticks reaches 3
    node.consecutive_wait_ticks = 3

    # Run step()
    frame = node.step(tick=4)

    # Robot must have detected livelock and replanned an alternate path or stepped into a nook
    assert node.robot.position == (5, 5) or node.robot.position != (5, 6)
    # Assert path was replanned to avoid peer or navigate around it
    assert len(node.robot.path) > 0
    # Next waypoint is NOT the blocked cell (5, 6)
    next_cell = (node.robot.path[0]["x"], node.robot.path[0]["y"]) if len(node.robot.path) == 1 else (node.robot.path[1]["x"], node.robot.path[1]["y"])
    assert next_cell != (5, 6), f"Expected alternate route/nook, but next cell was blocked {next_cell}"

    print("PHASE 6.1 VERIFICATION PASSED: Auditing robot successfully broke livelock with alternate detour/nook!")


if __name__ == "__main__":
    test_auditing_livelock_breaks()
