"""
test_part3_step4_data01.py — Validation for DATA-01 Inventory Ledger Version-Based Conflict Resolution.

Verifies:
1. Higher-version writes overwrite lower-version writes regardless of arrival order.
2. Delayed gossip with lower version does NOT overwrite newer local AMR state.
3. Same-version conflicts resolve deterministically by confidence, then by tick.
4. Multi-process concurrent writes converge deterministically without data corruption or lock starvation.
"""

from __future__ import annotations

import multiprocessing
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

import pytest

from app.models.inventory import ShelfRecord
from app.models.robot import AMRType, Robot, RobotState
from app.services.inventory_ledger import InventoryLedger
from app.services.robot_node import RobotNode
from app.transport.udp_transport import UdpTransport


@pytest.fixture
def temp_ledger_db():
    temp_dir = tempfile.mkdtemp(prefix="test_ledger_data01_")
    db_path = Path(temp_dir) / "inventory_test.db"
    ledger = InventoryLedger(db_path=db_path)
    yield ledger, db_path
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_higher_version_overwrites_lower_version_regardless_of_order(temp_ledger_db):
    """
    Validates that a higher-version record overwrites a lower-version record,
    and a delayed/out-of-order write with a lower version is rejected.
    """
    ledger, _ = temp_ledger_db

    # 1. Initial write: version 1, count 50
    rec_v1 = ShelfRecord(
        shelf_id="POD-TEST-01",
        x=5,
        y=5,
        current_box_count=50,
        sku_manifest={"SKU-A": 50},
        last_audited_tick=10,
        confidence=0.9,
        version=1,
    )
    assert ledger.upsert_shelf(rec_v1) is True
    saved = ledger.get_shelf("POD-TEST-01")
    assert saved is not None
    assert saved.version == 1
    assert saved.current_box_count == 50

    # 2. Update to version 2, count 30
    rec_v2 = ShelfRecord(
        shelf_id="POD-TEST-01",
        x=5,
        y=5,
        current_box_count=30,
        sku_manifest={"SKU-A": 30},
        last_audited_tick=20,
        confidence=0.8,  # even with lower confidence, higher version wins
        version=2,
    )
    assert ledger.upsert_shelf(rec_v2) is True
    saved = ledger.get_shelf("POD-TEST-01")
    assert saved.version == 2
    assert saved.current_box_count == 30

    # 3. Delayed out-of-order write arrives with version 1, count 100, higher confidence/tick
    rec_delayed_v1 = ShelfRecord(
        shelf_id="POD-TEST-01",
        x=5,
        y=5,
        current_box_count=100,
        sku_manifest={"SKU-A": 100},
        last_audited_tick=999,  # higher tick
        confidence=1.0,         # higher confidence
        version=1,              # lower version!
    )
    accepted = ledger.upsert_shelf(rec_delayed_v1)
    assert accepted is False, "Lower version write must be rejected by upsert_shelf"

    # Verify state was not corrupted or overwritten
    saved = ledger.get_shelf("POD-TEST-01")
    assert saved.version == 2
    assert saved.current_box_count == 30

    # 4. Monotonic upgrade to version 5, count 10
    rec_v5 = ShelfRecord(
        shelf_id="POD-TEST-01",
        x=5,
        y=5,
        current_box_count=10,
        sku_manifest={"SKU-A": 10},
        last_audited_tick=30,
        confidence=0.7,
        version=5,
    )
    assert ledger.upsert_shelf(rec_v5) is True
    saved = ledger.get_shelf("POD-TEST-01")
    assert saved.version == 5
    assert saved.current_box_count == 10

    # 5. Stale write with version 4 is rejected
    rec_v4 = ShelfRecord(
        shelf_id="POD-TEST-01",
        x=5,
        y=5,
        current_box_count=90,
        sku_manifest={"SKU-A": 90},
        last_audited_tick=100,
        confidence=1.0,
        version=4,
    )
    assert ledger.upsert_shelf(rec_v4) is False
    saved = ledger.get_shelf("POD-TEST-01")
    assert saved.version == 5
    assert saved.current_box_count == 10


def test_delayed_gossip_does_not_overwrite_newer_local_amr_state(temp_ledger_db):
    """
    Validates that when an AMR receives an INVENTORY_UPDATE over mesh gossip:
    - Delayed gossip with lower version is discarded.
    - Gossip with higher version is applied.
    """
    ledger, _ = temp_ledger_db

    peer_ports = {
        "AMR-TEST-01": 9821,
        "AMR-PEER-02": 9822,
    }
    t_amr = UdpTransport("AMR-TEST-01", 9821, peer_ports)
    t_peer = UdpTransport("AMR-PEER-02", 9822, peer_ports)

    try:
        node = RobotNode(
            "AMR-TEST-01",
            (2, 2),
            robot_type="GOODS_TO_PERSON",
            transport=t_amr,
            peer_ports=peer_ports,
            ledger=ledger,
        )

        node.local_inventory_cache["POD-A01"] = ShelfRecord(
            shelf_id="POD-A01",
            x=2,
            y=2,
            current_box_count=40,
            sku_manifest={"SKU-01": 40},
            last_audited_tick=50,
            confidence=1.0,
            version=3,
        )

        # 1. Simulate arrival of delayed gossip with version 2
        delayed_msg = {
            "type": "INVENTORY_UPDATE",
            "shelf_id": "POD-A01",
            "x": 2,
            "y": 2,
            "current_box_count": 99,
            "sku_manifest": {"SKU-01": 99},
            "confidence": 1.0,
            "tick": 75,
            "version": 2,  # Stale version!
            "source_robot_id": "AMR-PEER-02",
        }
        from app.security.hmac_envelope import sign_payload
        env = sign_payload(delayed_msg)
        t_peer.send("AMR-TEST-01", env)

        time.sleep(0.05)
        node.step(tick=51)

        # Local cache must still be version 3 with 40 boxes
        cached = node.local_inventory_cache["POD-A01"]
        assert cached.version == 3
        assert cached.current_box_count == 40

        # 2. Simulate arrival of newer gossip with version 4
        newer_msg = {
            "type": "INVENTORY_UPDATE",
            "shelf_id": "POD-A01",
            "x": 2,
            "y": 2,
            "current_box_count": 25,
            "sku_manifest": {"SKU-01": 25},
            "confidence": 0.85,
            "tick": 52,
            "version": 4,  # Higher version!
            "source_robot_id": "AMR-PEER-02",
        }
        env2 = sign_payload(newer_msg)
        t_peer.send("AMR-TEST-01", env2)

        time.sleep(0.05)
        node.step(tick=52)

        # Local cache must now be updated to version 4 with 25 boxes
        cached = node.local_inventory_cache["POD-A01"]
        assert cached.version == 4
        assert cached.current_box_count == 25
    finally:
        t_amr.close()
        t_peer.close()


def test_same_version_conflict_resolves_by_confidence_then_tick(temp_ledger_db):
    """
    Validates tie-breaking when versions are identical:
    - Higher confidence wins.
    - If confidence is identical, higher last_audited_tick wins.
    - Otherwise rejected.
    """
    ledger, _ = temp_ledger_db

    # Baseline: version 2, confidence 0.80, tick 10
    base = ShelfRecord(
        shelf_id="POD-TIE-01",
        x=1,
        y=1,
        current_box_count=50,
        sku_manifest={"SKU-T": 50},
        last_audited_tick=10,
        confidence=0.80,
        version=2,
    )
    assert ledger.upsert_shelf(base) is True

    # Same version, higher confidence (0.95 vs 0.80) -> WINS
    higher_conf = ShelfRecord(
        shelf_id="POD-TIE-01",
        x=1,
        y=1,
        current_box_count=55,
        sku_manifest={"SKU-T": 55},
        last_audited_tick=5,  # even with lower tick, higher confidence wins
        confidence=0.95,
        version=2,
    )
    assert ledger.upsert_shelf(higher_conf) is True
    saved = ledger.get_shelf("POD-TIE-01")
    assert saved.confidence == 0.95
    assert saved.current_box_count == 55

    # Same version, lower confidence (0.70 vs 0.95) -> REJECTED
    lower_conf = ShelfRecord(
        shelf_id="POD-TIE-01",
        x=1,
        y=1,
        current_box_count=80,
        sku_manifest={"SKU-T": 80},
        last_audited_tick=100,
        confidence=0.70,
        version=2,
    )
    assert ledger.upsert_shelf(lower_conf) is False
    assert ledger.get_shelf("POD-TIE-01").current_box_count == 55

    # Same version, identical confidence (0.95), higher tick (25 vs 5) -> WINS
    higher_tick = ShelfRecord(
        shelf_id="POD-TIE-01",
        x=1,
        y=1,
        current_box_count=60,
        sku_manifest={"SKU-T": 60},
        last_audited_tick=25,
        confidence=0.95,
        version=2,
    )
    assert ledger.upsert_shelf(higher_tick) is True
    saved = ledger.get_shelf("POD-TIE-01")
    assert saved.last_audited_tick == 25
    assert saved.current_box_count == 60

    # Same version, identical confidence (0.95), lower tick (20 vs 25) -> REJECTED
    lower_tick = ShelfRecord(
        shelf_id="POD-TIE-01",
        x=1,
        y=1,
        current_box_count=90,
        sku_manifest={"SKU-T": 90},
        last_audited_tick=20,
        confidence=0.95,
        version=2,
    )
    assert ledger.upsert_shelf(lower_tick) is False
    assert ledger.get_shelf("POD-TIE-01").current_box_count == 60


def _worker_concurrent_writes(db_path_str: str, worker_id: int, version: int, box_count: int, tick: int):
    """Worker function executed in separate OS process."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root / "backend" / "backend") not in sys.path:
        sys.path.insert(0, str(root / "backend" / "backend"))
    from app.services.inventory_ledger import InventoryLedger
    from app.models.inventory import ShelfRecord

    ledger = InventoryLedger(db_path=Path(db_path_str))
    record = ShelfRecord(
        shelf_id="POD-CONCURRENT",
        x=10,
        y=10,
        current_box_count=box_count,
        sku_manifest={"SKU-CONC": box_count},
        last_audited_tick=tick,
        confidence=1.0,
        version=version,
    )
    time.sleep(0.01 * worker_id)
    ledger.upsert_shelf(record)


def test_multiprocess_concurrent_write_deterministic_convergence(temp_ledger_db):
    """
    Spawns multiple independent OS processes attempting concurrent writes
    with distinct versions. Verifies that regardless of process scheduling,
    the highest version deterministically wins and WAL guarantees zero corruption.
    """
    ledger, db_path = temp_ledger_db

    # Baseline write: version 1
    base = ShelfRecord(
        shelf_id="POD-CONCURRENT",
        x=10,
        y=10,
        current_box_count=10,
        sku_manifest={"SKU-CONC": 10},
        last_audited_tick=1,
        confidence=1.0,
        version=1,
    )
    ledger.upsert_shelf(base)

    # 4 workers with out-of-order versions:
    # Worker 1: version 3, count 30, tick 15
    # Worker 2: version 7, count 70, tick 20  <-- Highest version, must win!
    # Worker 3: version 2, count 20, tick 99
    # Worker 4: version 5, count 50, tick 30
    workers_spec = [
        (1, 3, 30, 15),
        (2, 7, 70, 20),
        (3, 2, 20, 99),
        (4, 5, 50, 30),
    ]

    processes = []
    for w_id, ver, count, t in workers_spec:
        p = multiprocessing.Process(
            target=_worker_concurrent_writes,
            args=(str(db_path), w_id, ver, count, t),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=10.0)

    # Re-open ledger and verify winning state
    final_record = ledger.get_shelf("POD-CONCURRENT")
    assert final_record is not None
    assert final_record.version == 7, f"Expected deterministic winner version 7, got {final_record.version}"
    assert final_record.current_box_count == 70, f"Expected box count 70, got {final_record.current_box_count}"
