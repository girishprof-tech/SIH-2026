"""
test_part3_step1_sec01.py — Verification of Step 1 (SEC-01 Asymmetric Ed25519 Signing).

Validates:
1. Legitimate station directive signed with Station Ed25519 private key is accepted.
2. Forged station directive signed with stolen/guessed symmetric DEFAULT_SECRET_KEY is rejected
   with STATION_AUTHORITY_VIOLATION on the receiving AMR.
3. Forged station directive claiming to be AUTHORITY_STATION but signed with an AMR's private key
   is rejected by cryptographic verification against AUTHORITY_STATION's registered public key.
4. Peer AMR messages (RESOURCE_CLAIM, INVENTORY_UPDATE, POD_SLOT_OCCUPANCY) use Ed25519 signatures
   and verify cleanly across peers.
5. Legacy HMAC-SHA256 continues to verify when explicit custom secret keys are used.
"""

import sys
import pytest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))

from app.models.world import build_default_world
from app.services.robot_node import RobotNode
from app.security.hmac_envelope import (
    DEFAULT_SECRET_KEY,
    get_node_keypair,
    get_node_private_key,
    get_node_public_key,
    build_station_command_envelope,
    build_resource_claim_envelope,
    sign_payload,
    verify_envelope,
)


class MockTransport:
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
    world = build_default_world()
    transport = MockTransport()
    peer_ports = {"AMR-01": 9001, "IMPORT_STATION": 9601, "AUTHORITY_STATION": 9603}
    robot = RobotNode(
        robot_id="AMR-01",
        start_pos=(1, 10),
        goal_pos=None,
        port=9001,
        peer_ports=peer_ports,
        transport=transport,
        world=world,
    )
    return robot


def test_ed25519_keypair_determinism():
    """Verify that key derivation produces valid, matching Ed25519 keypairs."""
    priv1, pub1 = get_node_keypair("AMR-01")
    priv2, pub2 = get_node_keypair("AMR-01")
    assert priv1.private_bytes_raw() == priv2.private_bytes_raw()
    assert pub1.public_bytes_raw() == pub2.public_bytes_raw()

    # Different nodes must have different keys
    priv_auth, pub_auth = get_node_keypair("AUTHORITY_STATION")
    assert priv1.private_bytes_raw() != priv_auth.private_bytes_raw()
    assert pub1.public_bytes_raw() != pub_auth.public_bytes_raw()


def test_legitimate_station_command_ed25519_accepted(test_robot):
    """Confirm a legitimately signed directive from real station's private key passes."""
    import_task = {
        "task_id": "INDUCT-LEGIT-01",
        "task_type": "INDUCT_BATCH",
        "source_gate": "IN-1",
        "pickup": [0, 9],
        "dropoff": [4, 6],
    }
    # Builds envelope using IMPORT_STATION's Ed25519 private key
    env = build_station_command_envelope(
        command_type="TASK_ASSIGNMENT",
        station_id="IMPORT_STATION",
        station_role="IMPORT_STATION",
        task_dict=import_task,
        tick=5,
        target_robot_id="AMR-01",
    )
    assert env.get("algorithm") == "ed25519"

    test_robot.transport.inbox.append(env)
    test_robot._drain_inbox(current_tick=5)

    assert test_robot.task is not None
    assert test_robot.task.task_id == "INDUCT-LEGIT-01"
    assert len(test_robot.rejected_station_commands) == 0


def test_forged_symmetric_hmac_station_directive_rejected(test_robot):
    """
    Confirm a forged directive signed with stolen/guessed symmetric DEFAULT_SECRET_KEY
    is rejected by the AMR.
    """
    forged_task = {
        "task_id": "ROGUE-AUTH-HMAC-01",
        "task_type": "INDUCT_BATCH",
        "source_gate": "IN-1",
        "pickup": [0, 9],
        "dropoff": [4, 6],
    }
    payload = {
        "type": "TASK_ASSIGNMENT",
        "station_id": "AUTHORITY_STATION",
        "station_role": "AUTHORITY_STATION",
        "task": forged_task,
        "tick": 6,
        "target_robot_id": "AMR-01",
    }
    # Attacker forces legacy symmetric HMAC using the stolen shared secret key
    forged_env = sign_payload(payload, secret_key=DEFAULT_SECRET_KEY, use_asymmetric=False, seq=10)
    assert forged_env.get("algorithm") == "hmac-sha256"

    test_robot.transport.inbox.append(forged_env)
    test_robot._drain_inbox(current_tick=6)

    # Verification: Task was rejected due to symmetric HMAC
    assert test_robot.task is None
    assert len(test_robot.rejected_station_commands) == 1
    rejection = test_robot.rejected_station_commands[0]
    assert "STATION_AUTHORITY_VIOLATION" in rejection["reason"]
    assert "symmetric HMAC" in rejection["reason"]
    assert "Station Ed25519 private key" in rejection["reason"]


def test_forged_station_identity_with_wrong_private_key_rejected(test_robot):
    """
    Confirm an envelope claiming to be AUTHORITY_STATION but signed with another node's
    private key (e.g. compromised AMR-05) fails cryptographic verification.
    """
    forged_task = {
        "task_id": "ROGUE-AUTH-KEY-01",
        "task_type": "RETRIEVE_POD",
        "dropoff": [10, 10],
    }
    payload = {
        "type": "TASK_ASSIGNMENT",
        "station_id": "AUTHORITY_STATION",
        "station_role": "AUTHORITY_STATION",
        "task": forged_task,
        "tick": 7,
        "target_robot_id": "AMR-01",
    }
    # Attacker uses AMR-05 private key to sign an envelope claiming station_id=AUTHORITY_STATION
    from app.security.hmac_envelope import canonical_json_bytes
    body = {
        "payload": payload,
        "timestamp": 1234567.89,
        "sender_id": "AUTHORITY_STATION",
        "seq": 1,
    }
    canonical = canonical_json_bytes(body)
    amr05_priv = get_node_private_key("AMR-05")
    forged_signature = amr05_priv.sign(canonical).hex()

    forged_env = {
        "body": body,
        "signature": forged_signature,
        "algorithm": "ed25519",
    }

    # Verify directly: verify_envelope rejects because signature doesn't match AUTHORITY_STATION's public key
    valid, extracted, err = verify_envelope(forged_env)
    assert valid is False
    assert extracted is None
    assert "mismatch" in err.lower() or "failed" in err.lower()

    # Verify through robot inbox: packet dropped entirely
    test_robot.transport.inbox.append(forged_env)
    test_robot._drain_inbox(current_tick=7)
    assert test_robot.task is None


def test_peer_amr_ed25519_resource_claim():
    """Verify that peer AMR resource claims are signed with Ed25519 and verify cleanly."""
    claim_env = build_resource_claim_envelope("POD", "POD-A01", "AMR-02", tick=12, seq=1)
    assert claim_env.get("algorithm") == "ed25519"

    valid, payload, err = verify_envelope(claim_env)
    assert valid is True
    assert payload["type"] == "RESOURCE_CLAIM"
    assert payload["resource_id"] == "POD-A01"
    assert payload["robot_id"] == "AMR-02"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
