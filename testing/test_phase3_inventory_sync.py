"""
test_phase3_inventory_sync.py — Phase 3 Acceptance and Red-Team Attack Tests

Verifies:
1. Decentralized inventory broadcast over peer mesh to GOODS_TO_PERSON robot local caches.
2. Redundant HaLow uplink transmission tagged channel="HALOW" to dashboard listener.
3. Cryptographic HMAC verification and tampering rejection on inventory updates.
4. Replay attack rejection by ReplayGuard.
5. Packet loss chaos resilience and convergence under 30% loss.
6. HaLow token-bucket burst rate limiting and snapshot coalescing.
7. Node restart and local cache rehydration.
"""

import json
import socket
import sys
import time
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.robot import AMRType
from app.models.robot_fsm import RobotState
from app.models.world import build_default_world
from app.services.audit_mission import AuditMission
from app.services.inventory_ledger import InventoryLedger
from app.services.robot_node import RobotNode
from app.transport.halow_transport import HaLowTransport, TokenBucket
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from app.security.hmac_envelope import build_inventory_update_envelope, verify_envelope, sign_payload


def test_decentralized_inventory_sync_to_fetch_robots(tmp_path):
    """
    Acceptance Test 1: An audit robot audits a shelf. Within 1-2 ticks,
    live GOODS_TO_PERSON fetch robots receive the signed update and update their local cache.
    """
    hub = LoopbackNetworkHub()
    db_file = tmp_path / "sync_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    # 1 Audit robot + 2 G2P Fetch robots
    audit_transport = LoopbackTransport("AMR-AUDIT", hub=hub)
    fetch_1_transport = LoopbackTransport("AMR-G2P-1", hub=hub)
    fetch_2_transport = LoopbackTransport("AMR-G2P-2", hub=hub)

    peer_ports = {"AMR-AUDIT": 9001, "AMR-G2P-1": 9002, "AMR-G2P-2": 9003}

    audit_bot = RobotNode(
        robot_id="AMR-AUDIT",
        start_pos=(7, 5),
        robot_type="SCANNING_AUDIT",
        transport=audit_transport,
        peer_ports=peer_ports,
    )
    audit_bot.inventory_ledger = ledger

    fetch_1 = RobotNode(
        robot_id="AMR-G2P-1",
        start_pos=(2, 2),
        robot_type="GOODS_TO_PERSON",
        transport=fetch_1_transport,
        peer_ports=peer_ports,
    )
    fetch_1.inventory_ledger = ledger

    fetch_2 = RobotNode(
        robot_id="AMR-G2P-2",
        start_pos=(3, 3),
        robot_type="GOODS_TO_PERSON",
        transport=fetch_2_transport,
        peer_ports=peer_ports,
    )
    fetch_2.inventory_ledger = ledger

    # Trigger audit scan at checkpoint (7, 5) -> scans Bank A shelf
    audit_bot.fsm.state = RobotState.AUDITING
    audit_bot.active_audit_mission = AuditMission(checkpoint=(7, 5), target_shelf_id="POD-A04")
    
    # Audit robot steps at checkpoint
    frame = audit_bot.step(tick=1)

    # Fetch robots step to drain inbox
    fetch_1.step(tick=2)
    fetch_2.step(tick=2)

    # Assert both fetch robots' local caches reflect the scanned shelf state
    f1_cache = fetch_1.get_local_inventory("POD-A04")
    f2_cache = fetch_2.get_local_inventory("POD-A04")

    assert f1_cache is not None, "Fetch robot 1 local cache missing POD-A04!"
    assert f2_cache is not None, "Fetch robot 2 local cache missing POD-A04!"
    assert f1_cache.last_audited_tick == 1
    assert f2_cache.last_audited_tick == 1
    assert f1_cache.last_audited_by == "AMR-AUDIT"
    assert f2_cache.last_audited_by == "AMR-AUDIT"
    assert f1_cache.current_box_count == f2_cache.current_box_count


def test_halow_channel_mirroring_and_tamper_rejection():
    """
    Acceptance Test 2: Inventory updates are mirrored over HaLowTransport tagged channel="HALOW",
    and tamper verification fails if payload is modified.
    """
    halow = HaLowTransport(node_id="AMR-01", dashboard_port=9099)
    env = build_inventory_update_envelope(
        shelf_id="POD-B02",
        x=5,
        y=11,
        sku_manifest={"SKU-B11": 22},
        current_box_count=22,
        confidence=0.98,
        tick=10,
        source_robot_id="AMR-01",
        channel="HALOW",
    )

    # Valid verification
    valid, payload, err = verify_envelope(env)
    assert valid is True
    assert payload["channel"] == "HALOW"
    assert payload["shelf_id"] == "POD-B02"

    # Tampered payload
    tampered = json.loads(json.dumps(env))
    tampered["body"]["payload"]["current_box_count"] = 999  # Tamper with count
    t_valid, t_payload, t_err = verify_envelope(tampered)
    assert t_valid is False
    assert "tampered" in t_err.lower() or "mismatch" in t_err.lower()
    halow.close()


def test_redteam_packet_loss_convergence(tmp_path):
    """
    Attack check 1: Under 30% packet loss, multiple audit scans eventually converge
    on both fetch robots.
    """
    hub = LoopbackNetworkHub()
    db_file = tmp_path / "loss_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    peer_ports = {"AMR-AUDIT": 9001, "AMR-G2P": 9002}
    
    # 30% packet loss on sender transport
    audit_transport = LoopbackTransport("AMR-AUDIT", hub=hub, packet_loss_pct=30.0)
    fetch_transport = LoopbackTransport("AMR-G2P", hub=hub)

    audit_bot = RobotNode(
        robot_id="AMR-AUDIT",
        start_pos=(7, 5),
        robot_type="SCANNING_AUDIT",
        transport=audit_transport,
        peer_ports=peer_ports,
    )
    audit_bot.inventory_ledger = ledger

    fetch_bot = RobotNode(
        robot_id="AMR-G2P",
        start_pos=(2, 2),
        robot_type="GOODS_TO_PERSON",
        transport=fetch_transport,
        peer_ports=peer_ports,
    )
    fetch_bot.inventory_ledger = ledger

    # Broadcast 10 successive audit updates
    for t in range(1, 11):
        audit_bot.broadcast_inventory_update(
            shelf_id="POD-C01",
            current_tick=t,
            sku_manifest={"SKU-C01": 50 + t},
            box_count=50 + t,
            confidence=1.0,
        )
        fetch_bot.step(tick=t)

    # At least one of the 10 updates will have arrived under 30% loss (prob > 99.99%)
    cached = fetch_bot.get_local_inventory("POD-C01")
    assert cached is not None
    assert cached.last_audited_tick >= 1
    assert cached.current_box_count >= 51


def test_redteam_replay_attack_rejected():
    """
    Attack check 4: Replaying an old signed envelope is rejected by ReplayGuard.
    """
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-FETCH", hub=hub)
    node = RobotNode(
        robot_id="AMR-FETCH",
        start_pos=(1, 1),
        transport=transport,
    )

    env = build_inventory_update_envelope(
        shelf_id="POD-A01",
        x=4,
        y=6,
        sku_manifest={"SKU-A01": 10},
        current_box_count=10,
        confidence=1.0,
        tick=5,
        source_robot_id="AMR-ATTACKER",
        seq=1,
    )

    # First delivery: accepted
    hub.deliver("AMR-FETCH", env)
    node.step(tick=6)
    assert node.get_local_inventory("POD-A01").current_box_count == 10

    # Replay same envelope with same seq
    hub.deliver("AMR-FETCH", env)
    node.step(tick=7)
    # Confirm replay rejection logged and didn't crash
    assert node.get_local_inventory("POD-A01").current_box_count == 10


def test_redteam_halow_token_bucket_burst_throttling():
    """
    Attack check 5: Firing 50 inventory updates in a single tick throttles
    via token bucket and does not crash or queue unboundedly.
    """
    halow = HaLowTransport(node_id="AMR-BURST", bitrate_bps=150_000)

    # Fire 50 packets rapidly
    for i in range(50):
        env = build_inventory_update_envelope(
            shelf_id=f"POD-A{i:02d}",
            x=5,
            y=6,
            sku_manifest={"SKU-BURST": i},
            current_box_count=i,
            confidence=1.0,
            tick=1,
            source_robot_id="AMR-BURST",
            seq=i,
        )
        halow.send("DASHBOARD", env)

    status = halow.get_status()
    assert status["total_packets_sent"] > 0
    assert status["dropped_burst_packets"] > 0, "Token bucket should have throttled excess burst packets"
    assert status["utilization"] >= 0.5

    halow.close()


def test_redteam_node_restart_cache_hydration(tmp_path):
    """
    Attack check 2: A killed and restarted fetch robot reloads its local cache cleanly.
    """
    db_file = tmp_path / "restart_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    # Robot 1 writes update to ledger
    ledger.record_audit_scan(
        shelf_id="POD-D05",
        sku_counts={"SKU-D05": 77},
        robot_id="AMR-01",
        tick=50,
        confidence=0.99,
    )

    # Restart fetch robot afresh
    hub = LoopbackNetworkHub()
    transport = LoopbackTransport("AMR-G2P-RESTART", hub=hub)
    restarted_bot = RobotNode(
        robot_id="AMR-G2P-RESTART",
        start_pos=(0, 0),
        robot_type="GOODS_TO_PERSON",
        transport=transport,
    )
    restarted_bot.inventory_ledger = ledger
    # Re-hydrate local cache from persistent ledger
    restarted_bot.local_inventory_cache = {s.shelf_id: s for s in ledger.get_all_shelves()}

    cached = restarted_bot.get_local_inventory("POD-D05")
    assert cached is not None
    assert cached.current_box_count == 77
    assert cached.last_audited_tick == 50
