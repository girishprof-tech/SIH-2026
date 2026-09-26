"""
test_step2_command_authorization.py — Rigorous Verification and Attack Tests for Step 2 Command Authorization.

Validates the receiving-end zero-trust command authorization boundary implemented in RobotNode:
1. Attack 1: ExportStation issues an Import-dock task (INDUCT_BATCH or IN-1).
   Confirm AMR rejects the command, logs the rejection reason with STATION_AUTHORITY_VIOLATION,
   and appends to rejected_station_commands without accepting/executing the task.
2. Attack 2: ImportStation issues an Export-dock task (CONSOLIDATE_EXPORT or OUT-1).
   Confirm AMR rejects the command with STATION_AUTHORITY_VIOLATION and does not execute.
3. Attack 3: Unsigned / forged command claiming station_role='AUTHORITY_STATION' without HMAC envelope.
   Confirm AMR rejects the command due to missing HMAC cryptographic signature.
4. Attack 4: Command carrying unrecognized or invalid station_role.
   Confirm AMR rejects the command.
5. Legitimate Flow 1: ImportStation sends valid INDUCT_BATCH task.
   Confirm AMR accepts the authorized station directive.
6. Legitimate Flow 2: ExportStation sends valid CONSOLIDATE_EXPORT task.
   Confirm AMR accepts the authorized station directive.
7. Legitimate Flow 3: AuthorityStation sends an override command for any task type.
   Confirm AMR accepts the authorized station directive.
"""

import sys
import time
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))

from app.models.world import build_default_world
from app.services.robot_node import RobotNode
from app.security.hmac_envelope import (
    DEFAULT_SECRET_KEY,
    build_station_command_envelope,
    sign_payload,
)


class MockTransport:
    """Mock transport that delivers queued envelopes on recv_all."""

    def __init__(self):
        self.inbox = []
        self.outbox = []

    def send(self, peer_id, payload):
        self.outbox.append((peer_id, payload))

    def recv_all(self):
        msgs = list(self.inbox)
        self.inbox.clear()
        return msgs

    def set_packet_loss(self, pct):
        pass

    def close(self):
        pass


@pytest.fixture
def test_robot():
    """Builds a test AMR-01 RobotNode with MockTransport for zero-network testing."""
    world = build_default_world()
    peer_ports = {
        "AMR-01": 9001,
        "IMPORT_STATION": 9601,
        "EXPORT_STATION": 9602,
        "AUTHORITY_STATION": 9603,
    }
    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(2, 4),
        goal_pos=None,  # Genuinely idle
        urgency=1,
        battery_pct=100.0,
        obstacles=world.static_obstacles,
        port=9001,
        peer_ports=peer_ports,
        robot_type="GOODS_TO_PERSON",
        enable_idle_audit=False,
    )
    # Replace transport with MockTransport
    node.transport = MockTransport()
    return node


def test_attack_export_station_command_targeting_import_task(test_robot):
    """
    Attack Test 1: Export Station attempts to command an Import-dock task.
    AMR MUST reject it with explicit STATION_AUTHORITY_VIOLATION and not assign the task.
    """
    import_task = {
        "task_id": "INDUCT-FORGED-01",
        "task_type": "INDUCT_BATCH",
        "source_gate": "IN-1",
        "pickup": [0, 9],
        "dropoff": [4, 6],
    }
    # Build signed envelope originating from EXPORT_STATION
    env = build_station_command_envelope(
        command_type="TASK_ASSIGNMENT",
        station_id="EXPORT_STATION",
        station_role="EXPORT_STATION",
        task_dict=import_task,
        tick=5,
        target_robot_id="AMR-01",
    )

    test_robot.transport.inbox.append(env)
    test_robot._drain_inbox(current_tick=5)

    # Verification: Task was NOT accepted
    assert test_robot.task is None
    assert len(test_robot.rejected_station_commands) == 1
    rejection = test_robot.rejected_station_commands[0]
    assert "STATION_AUTHORITY_VIOLATION" in rejection["reason"]
    assert "EXPORT_STATION" in rejection["reason"]
    assert "INDUCT-FORGED-01" in rejection["reason"]
    assert "ExportStation has no authority over import" in rejection["reason"]


def test_attack_import_station_command_targeting_export_task(test_robot):
    """
    Attack Test 2: Import Station attempts to command an Export-dock task.
    AMR MUST reject it with explicit STATION_AUTHORITY_VIOLATION.
    """
    export_task = {
        "task_id": "EXPORT-FORGED-01",
        "task_type": "CONSOLIDATE_EXPORT",
        "destination_gate": "OUT-1",
        "dropoff": [29, 9],
    }
    env = build_station_command_envelope(
        command_type="TASK_ASSIGNMENT",
        station_id="IMPORT_STATION",
        station_role="IMPORT_STATION",
        task_dict=export_task,
        tick=6,
        target_robot_id="AMR-01",
    )

    test_robot.transport.inbox.append(env)
    test_robot._drain_inbox(current_tick=6)

    assert test_robot.task is None
    assert len(test_robot.rejected_station_commands) == 1
    rejection = test_robot.rejected_station_commands[0]
    assert "STATION_AUTHORITY_VIOLATION" in rejection["reason"]
    assert "IMPORT_STATION" in rejection["reason"]
    assert "EXPORT-FORGED-01" in rejection["reason"]
    assert "ImportStation has no authority over export" in rejection["reason"]


def test_attack_unsigned_station_command(test_robot):
    """
    Attack Test 3: Unsigned payload claims AuthorityStation role.
    AMR MUST reject it because it lacks HMAC cryptographic signature.
    """
    fake_msg = {
        "type": "TASK_ASSIGNMENT",
        "station_role": "AUTHORITY_STATION",
        "station_id": "AUTHORITY_STATION",
        "task": {
            "task_id": "ROGUE-AUTH-01",
            "task_type": "RETRIEVE_POD",
            "dropoff": [10, 10],
        },
    }
    # Delivered raw without signature envelope
    test_robot.transport.inbox.append(fake_msg)
    test_robot._drain_inbox(current_tick=7)

    assert test_robot.task is None
    assert len(test_robot.rejected_station_commands) == 1
    rejection = test_robot.rejected_station_commands[0]
    assert "STATION_AUTHORITY_VIOLATION" in rejection["reason"]
    assert "lacks a valid HMAC-SHA256 signature envelope" in rejection["reason"]


def test_attack_unrecognized_station_role(test_robot):
    """
    Attack Test 4: Signed command claims unrecognized/tampered role.
    AMR MUST reject it.
    """
    task = {"task_id": "TAMPERED-01", "task_type": "STANDARD", "dropoff": [5, 5]}
    payload = {
        "type": "TASK_ASSIGNMENT",
        "station_role": "FAKE_ROOT_STATION",
        "station_id": "FAKE_STATION",
        "task": task,
        "tick": 8,
    }
    signed_env = sign_payload(payload, secret_key=DEFAULT_SECRET_KEY, seq=1)

    test_robot.transport.inbox.append(signed_env)
    test_robot._drain_inbox(current_tick=8)

    assert test_robot.task is None
    assert len(test_robot.rejected_station_commands) == 1
    assert "unrecognized station_role 'FAKE_ROOT_STATION'" in test_robot.rejected_station_commands[0]["reason"]


def test_authorized_import_station_command_accepted(test_robot):
    """
    Legitimate Flow 1: Import Station sends valid INDUCT_BATCH task.
    AMR accepts and transitions.
    """
    valid_task = {
        "task_id": "INDUCT-LEGIT-01",
        "task_type": "INDUCT_BATCH",
        "source_gate": "IN-1",
        "pickup": [0, 9],
        "dropoff": [4, 6],
    }
    env = build_station_command_envelope(
        command_type="TASK_ASSIGNMENT",
        station_id="IMPORT_STATION",
        station_role="IMPORT_STATION",
        task_dict=valid_task,
        tick=9,
        target_robot_id="AMR-01",
    )

    test_robot.transport.inbox.append(env)
    test_robot._drain_inbox(current_tick=9)

    assert len(test_robot.rejected_station_commands) == 0
    assert test_robot.task is not None
    assert test_robot.task.task_id == "INDUCT-LEGIT-01"


def test_authorized_authority_station_override_accepted(test_robot):
    """
    Legitimate Flow 2: Authority Station issues command for any task type.
    AMR validates full authority and accepts.
    """
    auth_task = {
        "task_id": "AUTH-OVERRIDE-01",
        "task_type": "RETRIEVE_POD",
        "dropoff": [15, 14],
        "target_shelf_id": "POD-A01",
    }
    env = build_station_command_envelope(
        command_type="TASK_ASSIGNMENT",
        station_id="AUTHORITY_STATION",
        station_role="AUTHORITY_STATION",
        task_dict=auth_task,
        tick=10,
        target_robot_id="AMR-01",
    )

    test_robot.transport.inbox.append(env)
    test_robot._drain_inbox(current_tick=10)

    assert len(test_robot.rejected_station_commands) == 0
    assert test_robot.task is not None
    assert test_robot.task.task_id == "AUTH-OVERRIDE-01"
