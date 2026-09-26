"""
inventory_ledger.py — SQLite-backed crash-safe Inventory Ledger for SIH26123.

Provides persistent, ACID-compliant storage for warehouse pod inventory states
with concurrent multi-process writer support using SQLite WAL mode.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.models.inventory import ShelfRecord
from app.models.world import WorldConfig, build_default_world

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data") / "inventory.db"


class InventoryLedger:
    """
    SQLite-backed persistent inventory ledger.
    Safe for concurrent access across multiple robot OS processes.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a connection with WAL mode and sensible timeout."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level=None,  # We manage transactions explicitly
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        """Initialize ledger tables if they do not exist."""
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shelves (
                    shelf_id TEXT PRIMARY KEY,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    capacity_boxes INTEGER NOT NULL DEFAULT 100,
                    current_box_count INTEGER NOT NULL DEFAULT 0,
                    sku_manifest TEXT NOT NULL DEFAULT '{}',
                    last_audited_tick INTEGER NOT NULL DEFAULT 0,
                    last_audited_by TEXT,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shelf_id TEXT NOT NULL,
                    robot_id TEXT NOT NULL,
                    tick INTEGER NOT NULL,
                    scan_manifest TEXT NOT NULL,
                    box_count INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute("COMMIT;")

    def upsert_shelf(self, shelf: ShelfRecord) -> None:
        """Insert or update a shelf record in an atomic transaction."""
        manifest_json = json.dumps(shelf.sku_manifest)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                """
                INSERT INTO shelves (
                    shelf_id, x, y, capacity_boxes, current_box_count,
                    sku_manifest, last_audited_tick, last_audited_by, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(shelf_id) DO UPDATE SET
                    x=excluded.x,
                    y=excluded.y,
                    capacity_boxes=excluded.capacity_boxes,
                    current_box_count=excluded.current_box_count,
                    sku_manifest=excluded.sku_manifest,
                    last_audited_tick=excluded.last_audited_tick,
                    last_audited_by=excluded.last_audited_by,
                    confidence=excluded.confidence,
                    updated_at=CURRENT_TIMESTAMP;
                """,
                (
                    shelf.shelf_id,
                    shelf.x,
                    shelf.y,
                    shelf.capacity_boxes,
                    shelf.current_box_count,
                    manifest_json,
                    shelf.last_audited_tick,
                    shelf.last_audited_by,
                    shelf.confidence,
                ),
            )
            conn.execute("COMMIT;")

    def record_audit_scan(
        self,
        shelf_id: str,
        sku_counts: Dict[str, int],
        robot_id: str,
        tick: int,
        confidence: float = 1.0,
    ) -> ShelfRecord:
        """
        Record a verified audit scan for a shelf, updating its manifest, count,
        last_audited metadata, and logging an audit event.
        """
        box_count = sum(sku_counts.values())
        manifest_json = json.dumps(sku_counts)

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            # Fetch existing coordinates or default to (0, 0)
            cursor = conn.execute("SELECT x, y, capacity_boxes FROM shelves WHERE shelf_id = ?", (shelf_id,))
            row = cursor.fetchone()
            if row:
                x, y, cap = row["x"], row["y"], row["capacity_boxes"]
            else:
                x, y, cap = 0, 0, 100

            conn.execute(
                """
                INSERT INTO shelves (
                    shelf_id, x, y, capacity_boxes, current_box_count,
                    sku_manifest, last_audited_tick, last_audited_by, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(shelf_id) DO UPDATE SET
                    current_box_count=excluded.current_box_count,
                    sku_manifest=excluded.sku_manifest,
                    last_audited_tick=excluded.last_audited_tick,
                    last_audited_by=excluded.last_audited_by,
                    confidence=excluded.confidence,
                    updated_at=CURRENT_TIMESTAMP;
                """,
                (shelf_id, x, y, cap, box_count, manifest_json, tick, robot_id, confidence),
            )

            conn.execute(
                """
                INSERT INTO audit_logs (shelf_id, robot_id, tick, scan_manifest, box_count, confidence)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (shelf_id, robot_id, tick, manifest_json, box_count, confidence),
            )
            conn.execute("COMMIT;")

        return ShelfRecord(
            shelf_id=shelf_id,
            x=x,
            y=y,
            capacity_boxes=cap,
            current_box_count=box_count,
            sku_manifest=sku_counts,
            last_audited_tick=tick,
            last_audited_by=robot_id,
            confidence=confidence,
        )

    def get_shelf(self, shelf_id: str, current_tick: Optional[int] = None) -> Optional[ShelfRecord]:
        """Retrieve a shelf record by ID with optionally decayed confidence."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM shelves WHERE shelf_id = ?", (shelf_id,))
            row = cursor.fetchone()
            if not row:
                return None

            record = ShelfRecord(
                shelf_id=row["shelf_id"],
                x=row["x"],
                y=row["y"],
                capacity_boxes=row["capacity_boxes"],
                current_box_count=row["current_box_count"],
                sku_manifest=json.loads(row["sku_manifest"]),
                last_audited_tick=row["last_audited_tick"],
                last_audited_by=row["last_audited_by"],
                confidence=row["confidence"],
            )
            if current_tick is not None:
                record.confidence = record.compute_decayed_confidence(current_tick)
            return record

    def get_all_shelves(self, current_tick: Optional[int] = None) -> List[ShelfRecord]:
        """Retrieve all shelf records ordered by shelf_id."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM shelves ORDER BY shelf_id ASC;")
            records = []
            for row in cursor.fetchall():
                rec = ShelfRecord(
                    shelf_id=row["shelf_id"],
                    x=row["x"],
                    y=row["y"],
                    capacity_boxes=row["capacity_boxes"],
                    current_box_count=row["current_box_count"],
                    sku_manifest=json.loads(row["sku_manifest"]),
                    last_audited_tick=row["last_audited_tick"],
                    last_audited_by=row["last_audited_by"],
                    confidence=row["confidence"],
                )
                if current_tick is not None:
                    rec.confidence = rec.compute_decayed_confidence(current_tick)
                records.append(rec)
            return records

    def get_shelves_stale_since(self, tick_threshold: int) -> List[ShelfRecord]:
        """Retrieve shelves that have not been audited since tick_threshold."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM shelves WHERE last_audited_tick <= ? ORDER BY last_audited_tick ASC;",
                (tick_threshold,),
            )
            records = []
            for row in cursor.fetchall():
                records.append(
                    ShelfRecord(
                        shelf_id=row["shelf_id"],
                        x=row["x"],
                        y=row["y"],
                        capacity_boxes=row["capacity_boxes"],
                        current_box_count=row["current_box_count"],
                        sku_manifest=json.loads(row["sku_manifest"]),
                        last_audited_tick=row["last_audited_tick"],
                        last_audited_by=row["last_audited_by"],
                        confidence=row["confidence"],
                    )
                )
            return records

    def seed_default_inventory(self, world: Optional[WorldConfig] = None, force: bool = False) -> None:
        """
        Seed initial inventory data for all pod slots in the warehouse.
        Generates deterministic, realistic SKU manifests for every pod slot once.
        """
        if world is None:
            world = build_default_world()

        with self._get_connection() as conn:
            if not force:
                cursor = conn.execute("SELECT COUNT(*) as cnt FROM shelves;")
                if cursor.fetchone()["cnt"] >= len(world.pod_slots):
                    return  # Already seeded

        # Deterministic seed based on slot name
        for shelf_id, (x, y) in world.pod_slots.items():
            # Seed PRNG for stable, reproducible manifests
            rng = random.Random(f"SEED-{shelf_id}")
            bank = shelf_id.split("-")[1][0] if "-" in shelf_id else "A"
            
            sku_1 = f"SKU-{bank}{rng.randint(10, 30):02d}"
            sku_2 = f"SKU-{bank}{rng.randint(31, 60):02d}"
            qty_1 = rng.randint(10, 35)
            qty_2 = rng.randint(5, 25)
            
            manifest = {sku_1: qty_1, sku_2: qty_2}
            total_boxes = qty_1 + qty_2
            
            record = ShelfRecord(
                shelf_id=shelf_id,
                x=x,
                y=y,
                capacity_boxes=80,
                current_box_count=total_boxes,
                sku_manifest=manifest,
                last_audited_tick=0,
                last_audited_by=None,
                confidence=1.0,
            )
            self.upsert_shelf(record)
        log.info(f"Seeded inventory for {len(world.pod_slots)} pod slots in {self.db_path}.")
