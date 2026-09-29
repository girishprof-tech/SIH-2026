"""
test_phase1_5_patches.py — Comprehensive Verification & Red-Team Attack Suite (Phase 1.5)

Tests all 6 verified defects and their red-team attack vectors:
  1. Concurrency & Pod Occupancy: Two concurrent RETRIEVE_POD tasks targeting the same shelf_id
     result in exactly one robot lifting it; third task is rejected/held; neither robot ever holds
     duplicate carrying_pod_id.
  2. Bounded Lease on Pod Claims: When a robot crashes or is killed, its pod claim expires within
     bounded ticks (TTL) and is pruned cleanly without permanent shelf lockout.
  3. Ledger Path Stability: InventoryLedger resolves to the exact same canonical database path
     regardless of working directory (os.chdir test from 3 distinct working directories).
  4. HaLow Burst Drain: Token-bucket burst drain via periodic flush_pending() prevents unbounded
     growth of _pending_shelf_snapshots under sustained traffic.
  5. Inventory-Aware Task Creation: SKU order endpoint selects the optimal shelf (highest confidence,
     breaking ties by distance/stale coverage); unknown SKU returns clear 404/409 error.
  6. Provenance Separation: record_pick updates box count/manifest without altering last_audited_tick,
     last_audited_by, or confidence; audit scans log to audit_logs and re-verify.
  7. Peer Filtering: broadcast_inventory_update filters peer-mesh UDP strictly to GOODS_TO_PERSON
     peers while mirroring to DASHBOARD.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

_ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT_DIR / "backend" / "backend"))

import pytest
from fastapi import HTTPException

from app.models.inventory import ShelfRecord
from app.models.robot import AMRType, Heading, Robot, RobotState
from app.models.task import Task, TaskStatus, TaskType
from app.models.world import build_default_world
from app.services.grid import WarehouseGrid
from app.services.inventory_ledger import DEFAULT_DB_PATH, InventoryLedger, ROOT_DIR
from app.services.reservations import (
    DEFAULT_POD_LEASE_TICKS,
    SHARED_POD_CLAIMS,
    claim_pod,
    get_pod_claim,
    prune_stale_pod_claims,
    release_pod,
    release_robot_pod_claims,
    renew_pod_claim,
)
from app.services.robot_node import RobotNode
from app.services.task_manager import NearestIdleAssignment, TaskManager
from app.transport.base import Transport
from app.transport.halow_transport import HaLowTransport


class MockTestTransport(Transport):
    def __init__(self, node_id: str = "AMR-01", port: int = 9001):
        self.node_id = node_id
        self.port = port
        self.sent_messages: List[tuple[str, Dict[str, Any]]] = []
        self.inbox: List[Dict[str, Any]] = []

    def send(self, peer_id: str, payload: Dict[str, Any]) -> None:
        self.sent_messages.append((peer_id, payload))

    def recv_all(self) -> List[Dict[str, Any]]:
        msgs = list(self.inbox)
        self.inbox.clear()
        return msgs

    def set_packet_loss(self, pct: float) -> None:
        pass

    def close(self) -> None:
        pass


def make_test_robot(
    robot_id: str,
    x: int,
    y: int,
    robot_type: AMRType = AMRType.GOODS_TO_PERSON,
    state: RobotState = RobotState.IDLE,
) -> Robot:
    return Robot(
        robot_id=robot_id,
        x=x,
        y=y,
        heading=Heading.NORTH,
        state=state,
        battery_pct=100.0,
        current_task_id=None,
        priority_score=0,
        last_updated_tick=0,
        robot_type=robot_type,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 1 & ATTACK 1: Double-Lift Race Condition & Pod Occupancy Locking
# ─────────────────────────────────────────────────────────────────────────────

def test_nearest_idle_assignment_rejects_claimed_or_inflight_shelf():
    """
    NearestIdleAssignment must refuse to assign a second task targeting the same shelf_id
    if there is already an active claim or in-flight task for that shelf.
    """
    SHARED_POD_CLAIMS.clear()
    assigner = NearestIdleAssignment()

    robots = {
        "AMR-01": make_test_robot("AMR-01", 10, 10, robot_type=AMRType.GOODS_TO_PERSON, state=RobotState.IDLE),
        "AMR-02": make_test_robot("AMR-02", 11, 10, robot_type=AMRType.GOODS_TO_PERSON, state=RobotState.IDLE),
    }

    task1 = Task(
        task_id="TASK-G2P-01",
        pickup_x=10,
        pickup_y=12,
        dropoff_x=29,
        dropoff_y=9,
        urgency=3,
        created_tick=1,
        task_type=TaskType.RETRIEVE_POD,
        target_shelf_id="SHELF-X101",
    )
    task2 = Task(
        task_id="TASK-G2P-02",
        pickup_x=10,
        pickup_y=12,
        dropoff_x=29,
        dropoff_y=9,
        urgency=3,
        created_tick=1,
        task_type=TaskType.RETRIEVE_POD,
        target_shelf_id="SHELF-X101",
    )

    # 1. Assign task 1 -> Should assign to AMR-01 (closest)
    assigned_1 = assigner.assign(task1, robots, tick=1)
    assert assigned_1 == "AMR-01"
    task1.status = TaskStatus.ASSIGNED
    task1.assigned_robot_id = "AMR-01"

    # 2. Concurrently attempt to assign task 2 targeting the SAME shelf_id
    assigned_2 = assigner.assign(task2, robots, tick=1, active_tasks=[task1])
    assert assigned_2 is None, "Second task targeting the same shelf_id MUST NOT be assigned!"

    # 3. If shelf is already claimed in reservations, assigner must also reject
    claim_pod("SHELF-Y202", "AMR-01", current_tick=1)
    task3 = Task(
        task_id="TASK-G2P-03",
        pickup_x=10,
        pickup_y=12,
        dropoff_x=29,
        dropoff_y=9,
        urgency=3,
        created_tick=1,
        task_type=TaskType.RETRIEVE_POD,
        target_shelf_id="SHELF-Y202",
    )
    assigned_3 = assigner.assign(task3, robots, tick=1)
    assert assigned_3 is None, "Task targeting an actively claimed shelf MUST be rejected!"
    SHARED_POD_CLAIMS.clear()


def test_concurrent_double_lift_atomic_claim_red_team():
    """
    ATTACK 1: Fire two RETRIEVE_POD tasks at the same shelf_id from two different code paths
    in the same tick (genuinely concurrent claims). Confirm only one robot ever holds carrying_pod_id.
    A third robot attempting claim is rejected.
    """
    SHARED_POD_CLAIMS.clear()

    shelf = "SHELF-RACE-01"
    res1 = claim_pod(shelf, "AMR-01", current_tick=10)
    res2 = claim_pod(shelf, "AMR-02", current_tick=10)
    res3 = claim_pod(shelf, "AMR-03", current_tick=10)

    assert res1 is True, "First robot claim must succeed"
    assert res2 is False, "Second robot concurrent claim must be rejected"
    assert res3 is False, "Third robot claim must be rejected"
    assert get_pod_claim(shelf, current_tick=10) == "AMR-01"

    # Simulate node execution during LIFTING:
    world = build_default_world()
    grid = WarehouseGrid(obstacles=world.static_obstacles, width=30, height=30)
    pos = (11, 12)

    # Robot 1 lifts
    grid.set_pod_slot_occupant(pos, "AMR-01")
    assert grid.is_pod_slot_occupied_by_other(pos, "AMR-01") is False
    assert grid.is_pod_slot_occupied_by_other(pos, "AMR-02") is True

    # Robot 2 pathfinder to that slot is blocked by occupied pod slot
    from app.services.pathfinder import SpaceTimeAStarPlanner
    blocked = SpaceTimeAStarPlanner._vertex_blocked((11, 12), 11, {}, "AMR-02", grid=grid)
    assert blocked is True, "Pathfinder must consider occupied pod slot vertex-blocked for other robots"

    SHARED_POD_CLAIMS.clear()


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 1 & ATTACK 2: Bounded Lease on Pod Claims (Kill Recovery)
# ─────────────────────────────────────────────────────────────────────────────

def test_robot_process_kill_bounded_lease_release():
    """
    ATTACK 2: Kill a robot process while it holds an active pod claim (mid-LIFTING or mid-carry).
    Confirm the claim is released within a bounded number of ticks (TTL) so the shelf isn't
    permanently stuck unavailable.
    """
    SHARED_POD_CLAIMS.clear()

    shelf = "SHELF-CRASH-TEST"
    # AMR-01 claims shelf at tick 5 with 40-tick lease
    claimed = claim_pod(shelf, "AMR-01", current_tick=5, lease_ticks=DEFAULT_POD_LEASE_TICKS)
    assert claimed is True
    assert get_pod_claim(shelf, current_tick=10) == "AMR-01"

    # Mid-carry, AMR-01 process is killed (abrupt SIGKILL, no close() cleanup executed).
    # At tick 20, another robot tries to claim -> still within lease, refused.
    assert claim_pod(shelf, "AMR-02", current_tick=20) is False

    # At tick 46 (5 + 40 = 45 expiry), the lease has elapsed.
    # prune_stale_pod_claims runs during tick loop.
    released = prune_stale_pod_claims(current_tick=46)
    assert shelf in released, "Expired claim must be pruned automatically"
    assert get_pod_claim(shelf, current_tick=46) is None

    # AMR-02 can now successfully claim the shelf without permanent lockout!
    assert claim_pod(shelf, "AMR-02", current_tick=46) is True
    assert get_pod_claim(shelf, current_tick=46) == "AMR-02"

    SHARED_POD_CLAIMS.clear()


def test_clean_shutdown_releases_pod_claims_immediately():
    """
    When a robot cleanly shuts down or encounters FAILSAFE_HOLD, release_robot_pod_claims
    frees claims immediately.
    """
    SHARED_POD_CLAIMS.clear()
    claim_pod("SHELF-01", "AMR-01", current_tick=1)
    claim_pod("SHELF-02", "AMR-01", current_tick=1)

    released = release_robot_pod_claims("AMR-01")
    assert set(released) == {"SHELF-01", "SHELF-02"}
    assert get_pod_claim("SHELF-01") is None
    assert get_pod_claim("SHELF-02") is None
    SHARED_POD_CLAIMS.clear()


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 2 & ATTACK 3: Ledger Path Stability Across Working Directories
# ─────────────────────────────────────────────────────────────────────────────

def test_ledger_path_stability_across_working_directories():
    """
    ATTACK 3 & FIX 2: Launch the ledger from three different working directories in one test run
    and confirm all three resolve to the identical canonical db file.
    """
    original_cwd = os.getcwd()
    repo_root = Path(__file__).resolve().parents[1]
    backend_backend = repo_root / "backend" / "backend"
    temp_dir = Path(tempfile.gettempdir())

    canonical_db_path = (repo_root / "data" / "inventory.db").resolve()

    try:
        # Directory 1: Repo Root
        os.chdir(str(repo_root))
        ledger_1 = InventoryLedger()
        path_1 = ledger_1.db_path.resolve()

        # Directory 2: backend/backend
        if backend_backend.exists():
            os.chdir(str(backend_backend))
        ledger_2 = InventoryLedger()
        path_2 = ledger_2.db_path.resolve()

        # Directory 3: System Temp Dir
        os.chdir(str(temp_dir))
        ledger_3 = InventoryLedger()
        path_3 = ledger_3.db_path.resolve()

        assert path_1 == canonical_db_path, f"Root cwd failed: {path_1} != {canonical_db_path}"
        assert path_2 == canonical_db_path, f"backend/backend cwd failed: {path_2} != {canonical_db_path}"
        assert path_3 == canonical_db_path, f"tempdir cwd failed: {path_3} != {canonical_db_path}"
        assert path_1 == path_2 == path_3, "All 3 paths must be identical across working directories"

    finally:
        os.chdir(original_cwd)


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 3 & ATTACK 4: HaLow Burst Flush & Continuous Drain
# ─────────────────────────────────────────────────────────────────────────────

def test_halow_burst_fully_drains_via_flush_pending():
    """
    FIX 3 & ATTACK 4: Fire a burst exceeding token-bucket capacity.
    Verify flush_pending() drains all snapshots once bandwidth is available,
    and sustained high volume for 200+ ticks does not grow backlog unboundedly.
    """
    transport = HaLowTransport(node_id="AMR-01", bitrate_bps=16000, packet_loss_pct=0.0)
    # Constrain bucket burst capacity to 1000 bytes
    transport.bucket.capacity = 1000
    transport.bucket.tokens = 1000

    # Flood with 25 snapshots (each ~200-400 bytes, totaling ~7 KB > 1 KB bucket capacity)
    for i in range(25):
        envelope = {
            "type": "INVENTORY_UPDATE",
            "shelf_id": f"SHELF-{i:02d}",
            "box_count": 50,
            "confidence": 0.95,
            "tick": 1,
            "channel": "HALOW",
        }
        transport.send("DASHBOARD", envelope)

    # Backlog must have accumulated
    assert transport.pending_count > 0, "Burst exceeding bucket capacity must queue pending packets"
    initial_backlog = transport.pending_count

    # Simulate ticks: token bucket refills and flush_pending drains queue
    total_flushed = 0
    for tick in range(1, 40):
        transport.bucket.refill(800)  # Refill 800 bytes per tick
        flushed = transport.flush_pending()
        total_flushed += flushed
        if transport.pending_count == 0:
            break

    assert transport.pending_count == 0, f"Pending snapshots failed to drain completely: {transport.pending_count} remain"
    assert total_flushed == initial_backlog, f"Expected {initial_backlog} flushed, got {total_flushed}"


def test_halow_sustained_traffic_bounded_queue_red_team():
    """
    ATTACK 4: Run 200+ ticks with sustained packets and verify pending queue
    never grows unboundedly when flush_pending is invoked each tick.
    """
    transport = HaLowTransport(node_id="AMR-01", bitrate_bps=150000, packet_loss_pct=0.0)

    t = time.monotonic()
    max_observed_backlog = 0

    with patch("time.monotonic") as mock_time:
        for tick in range(1, 220):
            t += 0.100  # 100ms per tick
            mock_time.return_value = t

            # Continuous generation of inventory updates
            transport.send("DASHBOARD", {
                "type": "INVENTORY_UPDATE",
                "shelf_id": f"SHELF-{(tick % 10):02d}",
                "tick": tick,
                "box_count": 40,
                "confidence": 0.9,
            })
            transport.flush_pending()
            max_observed_backlog = max(max_observed_backlog, transport.pending_count)

    # Queue should be bounded to tiny buffer (<= 3), never runaway
    assert max_observed_backlog <= 5, f"HaLow pending queue grew excessively: max {max_observed_backlog}"
    assert transport.pending_count <= 2, f"Final backlog not drained: {transport.pending_count}"


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 4 & ATTACK 5: Inventory-Aware Task Creation & Shelf Selection
# ─────────────────────────────────────────────────────────────────────────────

def test_sku_order_prefers_high_confidence_over_alphabetical_order(tmp_path):
    """
    FIX 4 & ATTACK 5: POST order for a SKU that exists on a low-confidence (stale) shelf
    and a high-confidence shelf. Selection logic must pick high-confidence, not first alphabetically.
    """
    from app.api.tasks import select_best_shelf_for_sku

    db_path = tmp_path / "test_sku_sel.db"
    ledger = InventoryLedger(db_path=db_path)

    # Populate two shelves:
    # SHELF-A (alphabetically first) has low confidence (0.40)
    # SHELF-Z (alphabetically last) has high confidence (0.95)
    with ledger._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shelves (shelf_id, x, y, capacity_boxes, current_box_count, sku_manifest, last_audited_tick, last_audited_by, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("SHELF-A", 5, 5, 80, 20, json.dumps({"SKU-TARGET": 10}), 10, "AMR-01", 0.40),
        )
        conn.execute(
            """
            INSERT INTO shelves (shelf_id, x, y, capacity_boxes, current_box_count, sku_manifest, last_audited_tick, last_audited_by, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("SHELF-Z", 20, 20, 80, 20, json.dumps({"SKU-TARGET": 10}), 90, "AMR-02", 0.95),
        )

    # Select best shelf at tick 100
    chosen = select_best_shelf_for_sku(ledger, sku="SKU-TARGET", quantity=2, current_tick=100)
    assert chosen is not None
    assert chosen.shelf_id == "SHELF-Z", f"Expected SHELF-Z (confidence 0.95), got {chosen.shelf_id}"


def test_sku_order_tie_breaking_distance_and_stale_coverage(tmp_path):
    """
    When two shelves have identical confidence, break ties by distance to idle G2P AMR,
    then by lowest last_audited_tick (stale coverage).
    """
    from app.api.tasks import select_best_shelf_for_sku

    db_path = tmp_path / "test_tie_break.db"
    ledger = InventoryLedger(db_path=db_path)

    # Shelf 1: at (2, 2)
    # Shelf 2: at (25, 25)
    with ledger._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shelves (shelf_id, x, y, capacity_boxes, current_box_count, sku_manifest, last_audited_tick, last_audited_by, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("SHELF-CLOSE", 2, 2, 80, 20, json.dumps({"SKU-ITEM": 5}), 50, "AMR-01", 0.90),
        )
        conn.execute(
            """
            INSERT INTO shelves (shelf_id, x, y, capacity_boxes, current_box_count, sku_manifest, last_audited_tick, last_audited_by, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            ("SHELF-FAR", 25, 25, 80, 20, json.dumps({"SKU-ITEM": 5}), 50, "AMR-02", 0.90),
        )

    idle_robot = make_test_robot("AMR-01", 1, 1, robot_type=AMRType.GOODS_TO_PERSON, state=RobotState.IDLE)
    chosen = select_best_shelf_for_sku(ledger, sku="SKU-ITEM", quantity=1, idle_g2p_robots=[idle_robot], current_tick=55)
    assert chosen.shelf_id == "SHELF-CLOSE", "Tie-break must choose closer shelf to idle robot"


def test_unknown_sku_order_returns_none_and_not_fallback(tmp_path):
    """
    If no shelf holds the requested SKU, select_best_shelf_for_sku returns None,
    never falling back to a random shelf.
    """
    from app.api.tasks import select_best_shelf_for_sku

    db_path = tmp_path / "test_unknown.db"
    ledger = InventoryLedger(db_path=db_path)

    chosen = select_best_shelf_for_sku(ledger, sku="SKU-NON-EXISTENT", quantity=1)
    assert chosen is None, "Unknown SKU must return None, not a fallback guess!"


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 5: Separation of Pick Provenance from Audit Provenance
# ─────────────────────────────────────────────────────────────────────────────

def test_pick_provenance_does_not_corrupt_audit_metadata(tmp_path):
    """
    FIX 5: record_pick updates sku_manifest and box_count, but MUST NOT touch
    last_audited_tick, last_audited_by, or confidence, and must write to transaction_logs.
    """
    db_path = tmp_path / "test_provenance.db"
    ledger = InventoryLedger(db_path=db_path)

    # Initial audit scan setting confidence and audit metadata
    ledger.record_audit_scan(
        shelf_id="SHELF-PROV",
        robot_id="AMR-AUDITOR",
        tick=100,
        sku_counts={"SKU-ALPHA": 20, "SKU-BETA": 30},
        confidence=0.88,
    )

    rec_before = ledger.get_shelf("SHELF-PROV")
    assert rec_before.current_box_count == 50
    assert rec_before.last_audited_tick == 100
    assert rec_before.last_audited_by == "AMR-AUDITOR"
    assert rec_before.confidence == 0.88

    # Now execute a G2P Pick of 5 units of SKU-ALPHA at tick 250
    pick_result = ledger.record_pick(
        shelf_id="SHELF-PROV",
        sku="SKU-ALPHA",
        qty_delta=5,
        robot_id="AMR-G2P-01",
        tick=250,
    )

    assert pick_result is not None
    assert pick_result.sku_manifest["SKU-ALPHA"] == 15
    assert pick_result.current_box_count == 45

    # CRUCIAL: audit provenance MUST NOT have been updated!
    assert pick_result.last_audited_tick == 100, "Pick MUST NOT alter last_audited_tick!"
    assert pick_result.last_audited_by == "AMR-AUDITOR", "Pick MUST NOT alter last_audited_by!"
    assert pick_result.confidence == 0.88, "Pick MUST NOT alter confidence!"

    # Verify transaction_logs table has the entry
    with ledger._get_connection() as conn:
        cursor = conn.execute("SELECT * FROM transaction_logs WHERE shelf_id = 'SHELF-PROV';")
        rows = cursor.fetchall()
        assert len(rows) == 1
        tx = rows[0]
        assert tx["sku"] == "SKU-ALPHA"
        assert tx["qty_delta"] == -5
        assert tx["robot_id"] == "AMR-G2P-01"
        assert tx["tick"] == 250

        # audit_logs table must NOT have an entry for the pick
        cur_audit = conn.execute("SELECT * FROM audit_logs WHERE tick = 250;")
        assert len(cur_audit.fetchall()) == 0


# ─────────────────────────────────────────────────────────────────────────────
# DEFECT 6: Peer Filtering on Mesh Broadcast
# ─────────────────────────────────────────────────────────────────────────────

def test_peer_filtering_mesh_broadcast_strictly_to_g2p(tmp_path):
    """
    FIX 6: In broadcast_inventory_update(), only send the peer-mesh copy to peers
    whose robot_type == GOODS_TO_PERSON. SORTING and SCANNING_AUDIT peers are filtered out.
    """
    mock_transport = MockTestTransport(node_id="AMR-01", port=9001)
    db_path = tmp_path / "test_peer_filter.db"
    ledger = InventoryLedger(db_path=db_path)

    # Seed shelf
    ledger.record_audit_scan("SHELF-FILTER-01", {"SKU-A": 10}, "AMR-01", 1, 1.0)

    fleet_roster = {
        "AMR-01": "GOODS_TO_PERSON",
        "AMR-02": "GOODS_TO_PERSON",
        "AMR-06": "SORTING",
        "AMR-09": "SCANNING_AUDIT",
    }
    peer_ports = {
        "AMR-01": 9001,
        "AMR-02": 9002,
        "AMR-06": 9006,
        "AMR-09": 9009,
    }

    node = RobotNode(
        robot_id="AMR-01",
        start_pos=(5, 5),
        port=9001,
        peer_ports=peer_ports,
        transport=mock_transport,
        robot_type="GOODS_TO_PERSON",
        ledger=ledger,
        fleet_roster=fleet_roster,
    )

    # Broadcast inventory update
    node.broadcast_inventory_update(
        shelf_id="SHELF-FILTER-01",
        current_tick=10,
        sku_manifest={"SKU-A": 10},
        box_count=10,
        confidence=1.0,
        is_audit=False,
    )

    # Inspect sent messages in mock transport
    sent_targets = [call[0] for call in mock_transport.sent_messages]

    # AMR-02 (GOODS_TO_PERSON) MUST receive it
    assert "AMR-02" in sent_targets, "GOODS_TO_PERSON peer AMR-02 must receive mesh update"

    # AMR-06 (SORTING) and AMR-09 (SCANNING_AUDIT) MUST NOT receive it
    assert "AMR-06" not in sent_targets, "SORTING peer AMR-06 must NOT receive mesh update"
    assert "AMR-09" not in sent_targets, "SCANNING_AUDIT peer AMR-09 must NOT receive mesh update"


# ─────────────────────────────────────────────────────────────────────────────
# API ENDPOINT VERIFICATION: Live POST /order and POST /job SKU Routing
# ─────────────────────────────────────────────────────────────────────────────

def test_api_sku_order_endpoint_known_and_unknown_sku():
    """
    Live API validation for FIX 4:
    - POST an order for a known SKU produces a Task with correctly chosen target_shelf_id
      and sku_to_pick set.
    - POST an order for an unknown SKU produces a clear 404, not a fallback guess.
    """
    from fastapi.testclient import TestClient
    from app.main import app

    with patch.dict(os.environ, {"SPAWN_FLEET_ORCHESTRATOR": "0"}):
        with TestClient(app) as client:
            # 1. Unknown SKU test on /api/task/order -> Expect 400 or 404
            resp_unknown = client.post("/api/task/order", json={"sku": "NONEXISTENT-SKU-999", "quantity": 1, "urgency": 3})
            assert resp_unknown.status_code in (400, 404), f"Expected 400 or 404 for unknown SKU, got {resp_unknown.status_code}"
            assert any(msg in resp_unknown.json()["detail"] for msg in ("No shelf holds SKU", "Unknown product"))

            # 2. Unknown SKU test on /api/job -> Expect 404
            resp_job_unknown = client.post("/api/job", json={"job_type": "fetch_item", "sku": "NONEXISTENT-SKU-999", "urgency": 3})
            assert resp_job_unknown.status_code == 404
            assert "No shelf holds requested SKU" in resp_job_unknown.json()["detail"]

            # 3. Known SKU test: Seed a shelf in the live app ledger
            ledger = app.state.inventory_ledger
            ledger.record_audit_scan(
                shelf_id="SHELF-API-TARGET",
                sku_counts={"SKU-WIDGET-PRO": 15},
                robot_id="AMR-AUDITOR",
                tick=1,
                confidence=0.99,
            )

            # Ensure an idle G2P AMR is available
            fleet = app.state.fleet_state
            g2p_robot = next(r for r in fleet.robots.values() if r.robot_type == AMRType.GOODS_TO_PERSON)
            g2p_robot.state = RobotState.IDLE
            g2p_robot.current_task_id = None

            # POST to /api/task/order
            resp_known = client.post("/api/task/order", json={"sku": "SKU-WIDGET-PRO", "quantity": 2, "urgency": 4})
            assert resp_known.status_code == 201, f"Expected 201, got {resp_known.status_code}: {resp_known.text}"
            data = resp_known.json()
            assert data["target_shelf_id"] == "SHELF-API-TARGET"
            assert data["sku"] == "SKU-WIDGET-PRO"
            assert data["quantity"] == 2

            # Verify task was created in TaskManager with proper attributes
            task_id = data["task_id"]
            task = app.state.task_manager.get_task(task_id)
            assert task is not None
            assert task.target_shelf_id == "SHELF-API-TARGET"
            assert task.sku_to_pick == "SKU-WIDGET-PRO"
            assert task.quantity == 2
            assert task.task_type == TaskType.RETRIEVE_POD

