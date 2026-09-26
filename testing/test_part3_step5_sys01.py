"""
test_part3_step5_sys01.py — Validation for SYS-01 Windows SQLite WAL Lock Contention & Resource Cleanup.

Verifies:
1. Rapid open/write/close cycles on Windows filesystem do NOT leak connection handles or cause `database is locked`.
2. Proper immediate file cleanup on Windows: temporary SQLite DB and WAL files can be deleted without `WinError 32: The process cannot access the file because it is being used by another process`.
3. Concurrent multi-process reads during active continuous writes without starvation or locking errors.
4. Ephemeral in-memory database (:memory:) functions properly without disk IO or lock contention.
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
from app.services.inventory_ledger import InventoryLedger


def test_rapid_open_write_close_cycles(tmp_path):
    """
    Rapidly creates, writes to, and closes multiple InventoryLedger instances
    against the same SQLite database file. Proves that connections are closed
    immediately upon operation exit and no lock contention occurs.
    """
    db_file = tmp_path / "rapid_cycles.db"

    for i in range(25):
        ledger = InventoryLedger(db_path=db_file)
        record = ShelfRecord(
            shelf_id=f"SHELF-{i:03d}",
            x=i,
            y=i,
            current_box_count=10 + i,
            sku_manifest={f"SKU-{i}": 10 + i},
            last_audited_tick=i,
            confidence=1.0,
            version=1,
        )
        assert ledger.upsert_shelf(record) is True
        fetched = ledger.get_shelf(f"SHELF-{i:03d}")
        assert fetched is not None
        assert fetched.current_box_count == 10 + i
        ledger.close()

    # Re-open and verify all 25 shelves exist
    with InventoryLedger(db_path=db_file) as ledger:
        all_shelves = ledger.get_all_shelves()
        assert len(all_shelves) == 25


def test_windows_immediate_temp_file_deletion_no_winerror32(tmp_path):
    """
    Simulates Windows automated test fixtures where a temporary database
    directory is created, written to by InventoryLedger, and immediately deleted.
    Under the previous flaw, unclosed connection handles caused WinError 32.
    With context-managed connections, directory deletion must succeed unconditionally.
    """
    test_dir = tmp_path / "ephemeral_run"
    test_dir.mkdir(parents=True, exist_ok=True)
    db_file = test_dir / "ephemeral.db"

    # Use context manager
    with InventoryLedger(db_path=db_file) as ledger:
        rec = ShelfRecord(
            shelf_id="POD-DEL-01",
            x=3,
            y=4,
            current_box_count=50,
            sku_manifest={"SKU-DEL": 50},
            last_audited_tick=1,
            confidence=1.0,
            version=1,
        )
        assert ledger.upsert_shelf(rec) is True
        assert ledger.get_shelf("POD-DEL-01") is not None

    # Immediately delete directory — must NOT raise PermissionError / WinError 32
    shutil.rmtree(test_dir)
    assert not test_dir.exists()


def test_in_memory_database_isolation_and_operations():
    """
    Validates that InventoryLedger supports :memory: for ephemeral test runs
    without creating any filesystem artifacts or file lock risks.
    """
    with InventoryLedger(db_path=":memory:") as mem_ledger:
        rec1 = ShelfRecord(
            shelf_id="POD-MEM-01",
            x=1,
            y=1,
            current_box_count=20,
            sku_manifest={"SKU-M": 20},
            last_audited_tick=5,
            confidence=1.0,
            version=1,
        )
        assert mem_ledger.upsert_shelf(rec1) is True

        rec2 = ShelfRecord(
            shelf_id="POD-MEM-02",
            x=2,
            y=2,
            current_box_count=35,
            sku_manifest={"SKU-M2": 35},
            last_audited_tick=6,
            confidence=0.9,
            version=1,
        )
        assert mem_ledger.upsert_shelf(rec2) is True

        all_records = mem_ledger.get_all_shelves()
        assert len(all_records) == 2
        assert mem_ledger.get_shelf("POD-MEM-01").current_box_count == 20
        assert mem_ledger.get_shelf("POD-MEM-02").current_box_count == 35


def _writer_process(db_path_str: str, num_writes: int, done_event):
    """Child process that continuously writes updates."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root / "backend" / "backend") not in sys.path:
        sys.path.insert(0, str(root / "backend" / "backend"))
    from app.services.inventory_ledger import InventoryLedger
    from app.models.inventory import ShelfRecord

    ledger = InventoryLedger(db_path=Path(db_path_str))
    for i in range(1, num_writes + 1):
        rec = ShelfRecord(
            shelf_id="POD-HOT",
            x=5,
            y=5,
            current_box_count=i,
            sku_manifest={"SKU-HOT": i},
            last_audited_tick=i,
            confidence=1.0,
            version=i,
        )
        ledger.upsert_shelf(rec)
        time.sleep(0.005)
    done_event.set()


def _reader_process(db_path_str: str, read_errors_counter, done_event):
    """Child process that continuously reads while writer is active."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root / "backend" / "backend") not in sys.path:
        sys.path.insert(0, str(root / "backend" / "backend"))
    from app.services.inventory_ledger import InventoryLedger

    ledger = InventoryLedger(db_path=Path(db_path_str))
    while not done_event.is_set():
        try:
            res = ledger.get_shelf("POD-HOT")
            # Should be None or valid ShelfRecord
            if res is not None:
                assert res.current_box_count >= 0
        except Exception:
            with read_errors_counter.get_lock():
                read_errors_counter.value += 1
        time.sleep(0.002)


def test_concurrent_multiprocess_reads_during_active_writes(tmp_path):
    """
    Spawns concurrent writer and reader processes.
    Validates SQLite WAL concurrency on Windows: readers never block writers
    and readers never raise database locked errors.
    """
    db_file = tmp_path / "concurrent_wal.db"
    # Seed table
    with InventoryLedger(db_path=db_file) as init_ledger:
        init_ledger.upsert_shelf(
            ShelfRecord(
                shelf_id="POD-HOT",
                x=5,
                y=5,
                current_box_count=0,
                sku_manifest={},
                last_audited_tick=0,
                confidence=1.0,
                version=0,
            )
        )

    done_event = multiprocessing.Event()
    read_errors = multiprocessing.Value("i", 0)

    p_writer = multiprocessing.Process(
        target=_writer_process,
        args=(str(db_file), 40, done_event),
    )
    p_reader1 = multiprocessing.Process(
        target=_reader_process,
        args=(str(db_file), read_errors, done_event),
    )
    p_reader2 = multiprocessing.Process(
        target=_reader_process,
        args=(str(db_file), read_errors, done_event),
    )

    p_reader1.start()
    p_reader2.start()
    p_writer.start()

    p_writer.join(timeout=15.0)
    done_event.set()
    p_reader1.join(timeout=5.0)
    p_reader2.join(timeout=5.0)

    assert read_errors.value == 0, f"Encountered {read_errors.value} read errors during concurrent write operations"

    with InventoryLedger(db_path=db_file) as ledger:
        final_rec = ledger.get_shelf("POD-HOT")
        assert final_rec is not None
        assert final_rec.version == 40
        assert final_rec.current_box_count == 40
