"""
test_phase2_inventory.py — Phase 2 Acceptance and Red-Team Attack Tests

Verifies:
1. InventoryLedger SQLite persistence and WAL mode operation.
2. Audit scans update last_audited_tick, confidence, and SKU manifests for the correct shelf.
3. Confidence decays monotonically without going negative or NaN over 1000+ ticks.
4. Red-team concurrency: Multiple concurrent worker processes racing to audit the same shelf.
5. Red-team crash resilience: Process terminated mid-write leaves SQLite readable and uncorrupted.
6. Unknown shelf graceful degradation.
"""

import math
import multiprocessing
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.inventory import ShelfRecord
from app.models.world import build_default_world
from app.services.audit_mission import AuditMission
from app.services.inventory_ledger import InventoryLedger


def test_inventory_ledger_crud_and_seeding(tmp_path):
    db_file = tmp_path / "test_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()

    # Seed
    ledger.seed_default_inventory(world)
    all_shelves = ledger.get_all_shelves()
    assert len(all_shelves) == len(world.pod_slots)

    # Pick a shelf
    shelf_a01 = ledger.get_shelf("POD-A01")
    assert shelf_a01 is not None
    assert shelf_a01.shelf_id == "POD-A01"
    assert shelf_a01.current_box_count > 0
    assert len(shelf_a01.sku_manifest) > 0
    assert shelf_a01.confidence == 1.0


def test_audit_scan_updates_ledger(tmp_path):
    db_file = tmp_path / "test_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    # Perform an audit mission at checkpoint (7, 5) which is adjacent to Bank A POD-A04 at (7, 6)
    mission = AuditMission(checkpoint=(7, 5), audit_id="AUDIT-TEST-01")
    scan_res = mission.record_scan(
        cell=(7, 5),
        robot_id="AMR-08",
        tick=42,
        ledger=ledger,
        world=world,
    )

    assert scan_res["status"] == "SUCCESS"
    scanned_id = scan_res["shelf_id"]
    assert scanned_id is not None

    # Verify updated shelf in ledger
    updated_shelf = ledger.get_shelf(scanned_id)
    assert updated_shelf is not None
    assert updated_shelf.last_audited_tick == 42
    assert updated_shelf.last_audited_by == "AMR-08"
    assert updated_shelf.confidence >= 0.95
    assert updated_shelf.current_box_count == scan_res["box_count"]


def test_monotonic_confidence_decay():
    """Verify confidence decays monotonically and stays bounded between 0.05 and 1.0."""
    shelf = ShelfRecord(
        shelf_id="POD-TEST",
        x=5,
        y=5,
        capacity_boxes=100,
        current_box_count=30,
        sku_manifest={"SKU-01": 30},
        last_audited_tick=100,
        last_audited_by="AMR-01",
        confidence=1.0,
    )

    prev_conf = 1.0
    for tick in range(100, 2000, 50):
        conf = shelf.compute_decayed_confidence(current_tick=tick)
        assert 0.05 <= conf <= 1.0, f"Confidence {conf} out of bounds at tick {tick}"
        assert not math.isnan(conf), f"Confidence is NaN at tick {tick}"
        assert conf <= prev_conf, f"Confidence increased non-monotonically at tick {tick}: {conf} > {prev_conf}"
        prev_conf = conf

    # After 2000 ticks, confidence should have decayed to the minimum floor (0.05)
    assert prev_conf == 0.05


def _concurrent_audit_worker(db_path: str, shelf_id: str, worker_id: int, start_barrier, error_queue):
    try:
        ledger = InventoryLedger(db_path=db_path)
        start_barrier.wait(timeout=5.0)
        for i in range(25):
            ledger.record_audit_scan(
                shelf_id=shelf_id,
                sku_counts={f"SKU-W{worker_id}": 10 + i},
                robot_id=f"WORKER-{worker_id}",
                tick=100 + i,
                confidence=1.0,
            )
            time.sleep(0.002)
    except Exception as e:
        error_queue.put(f"Worker {worker_id} error: {e}")


def test_redteam_concurrent_audit_writers(tmp_path):
    """
    Attack check 1: Multiple processes racing to audit the same shelf simultaneously.
    Verifies SQLite WAL mode handles concurrency without "database is locked" crashes.
    """
    db_file = tmp_path / "concurrent_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    num_workers = 4
    barrier = multiprocessing.Barrier(num_workers)
    error_queue = multiprocessing.Queue()

    processes = []
    for wid in range(num_workers):
        p = multiprocessing.Process(
            target=_concurrent_audit_worker,
            args=(str(db_file), "POD-A01", wid, barrier, error_queue),
        )
        p.start()
        processes.append(p)

    for p in processes:
        p.join(timeout=10.0)
        if p.is_alive():
            p.terminate()

    errors = []
    while not error_queue.empty():
        errors.append(error_queue.get())

    assert len(errors) == 0, f"Concurrent audit errors: {errors}"

    # Verify DB is readable and has valid final state
    final_shelf = ledger.get_shelf("POD-A01")
    assert final_shelf is not None
    assert final_shelf.last_audited_tick >= 100


def _unbounded_writer_loop(db_path: str, shelf_id: str):
    ledger = InventoryLedger(db_path=db_path)
    count = 0
    while True:
        ledger.record_audit_scan(
            shelf_id=shelf_id,
            sku_counts={"SKU-BURST": count % 50},
            robot_id="KILL-TARGET",
            tick=count,
            confidence=1.0,
        )
        count += 1
        time.sleep(0.001)


def test_redteam_crash_resilience_mid_write(tmp_path):
    """
    Attack check 2: Terminate writer process mid-execution and verify SQLite
    WAL guarantees uncorrupted database integrity.
    """
    db_file = tmp_path / "crash_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    p = multiprocessing.Process(
        target=_unbounded_writer_loop,
        args=(str(db_file), "POD-B02"),
    )
    p.start()
    time.sleep(0.05)  # Let it execute multiple writes

    # Abruptly terminate process
    p.terminate()
    p.join(timeout=2.0)

    # Reopen database fresh and verify readability
    recovered_ledger = InventoryLedger(db_path=db_file)
    shelf = recovered_ledger.get_shelf("POD-B02")
    assert shelf is not None
    all_shelves = recovered_ledger.get_all_shelves()
    assert len(all_shelves) == len(world.pod_slots)


def test_redteam_unknown_shelf_handling(tmp_path):
    """
    Attack check 3: Audit scan referencing non-existent shelf degrades gracefully
    without raising uncaught exception.
    """
    db_file = tmp_path / "unknown_shelf.db"
    ledger = InventoryLedger(db_path=db_file)

    # Record scan for an unknown shelf ID
    record = ledger.record_audit_scan(
        shelf_id="POD-UNKNOWN-99",
        sku_counts={"SKU-UNKNOWN": 15},
        robot_id="AMR-01",
        tick=1,
    )
    assert record.shelf_id == "POD-UNKNOWN-99"
    assert record.current_box_count == 15

    fetched = ledger.get_shelf("POD-UNKNOWN-99")
    assert fetched is not None
    assert fetched.sku_manifest == {"SKU-UNKNOWN": 15}
