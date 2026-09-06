"""
test_battery_estop.py — Verification of Battery Low trigger and Emergency Stop / Reset.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.core.config import get_settings
from app.models.robot_fsm import RobotState
from app.security.hmac_envelope import sign_payload
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from fastapi.testclient import TestClient
from app.main import app

cfg = get_settings()


def test_battery_low_trigger():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(1, 6),
        goal_pos=None,
        transport=transport,
    )
    # Set battery percentage below BATTERY_LOW_THRESHOLD (20.0)
    node.robot.battery_pct = 18.5
    assert node.fsm.state == RobotState.IDLE

    # Run step()
    frame = node.step(tick=1)

    # Robot must have transitioned to CHARGING
    assert node.fsm.state == RobotState.CHARGING, f"Expected CHARGING, got {node.fsm.state}"
    assert node.robot.state == RobotState.CHARGING
    assert frame["action"] == "CHARGING"
    print("PHASE 6.5a PASSED: Battery low threshold properly triggered transition to CHARGING!")


def test_estop_and_reset_control_messages():
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-01", hub=hub)
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(1, 6),
        goal_pos=None,
        transport=transport,
    )
    assert node.fsm.state == RobotState.IDLE

    # 1. Send EMERGENCY_STOP control message
    estop_msg = {
        "type": "EMERGENCY_STOP",
        "robot_id": "AMR-01",
        "sender_id": "CENTRAL_DISPATCH",
    }
    envelope = sign_payload(estop_msg, secret_key=node.secret_key)
    hub.deliver("AMR-01", envelope)

    # Run step to drain inbox
    node.step(tick=2)
    assert node.fsm.state == RobotState.EMERGENCY_STOP, f"Expected EMERGENCY_STOP, got {node.fsm.state}"
    assert node.robot.state == RobotState.EMERGENCY_STOP

    # 2. Send RESET control message
    reset_msg = {
        "type": "RESET",
        "robot_id": "AMR-01",
        "sender_id": "CENTRAL_DISPATCH",
    }
    reset_envelope = sign_payload(reset_msg, secret_key=node.secret_key)
    hub.deliver("AMR-01", reset_envelope)

    node.step(tick=3)
    assert node.fsm.state == RobotState.IDLE, f"Expected IDLE, got {node.fsm.state}"
    assert node.robot.state == RobotState.IDLE
    print("PHASE 6.5b PASSED: EMERGENCY_STOP and RESET control messages verified!")


def test_api_emergency_stop_endpoints():
    with TestClient(app) as client:
        # 1. Check list robots
        res = client.get("/api/robots/")
        assert res.status_code == 200
        robots = res.json()
        assert len(robots) > 0
        target_id = robots[0]["robot_id"]

        # 2. POST /api/robots/{id}/emergency_stop
        res_estop = client.post(f"/api/robots/{target_id}/emergency_stop")
        assert res_estop.status_code == 200
        assert res_estop.json()["status"] == "ok"

        # Check state updated
        res_get = client.get(f"/api/robots/{target_id}")
        assert res_get.status_code == 200
        assert res_get.json()["state"] == "EMERGENCY_STOP"

        # 3. POST /api/robots/{id}/reset
        res_reset = client.post(f"/api/robots/{target_id}/reset")
        assert res_reset.status_code == 200
        assert res_reset.json()["status"] == "ok"

        res_get2 = client.get(f"/api/robots/{target_id}")
        assert res_get2.status_code == 200
        assert res_get2.json()["state"] == "IDLE"

        print("PHASE 6.5c PASSED: REST API emergency_stop and reset endpoints verified!")


if __name__ == "__main__":
    test_battery_low_trigger()
    test_estop_and_reset_control_messages()
    test_api_emergency_stop_endpoints()
