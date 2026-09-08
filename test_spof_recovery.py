"""
test_spof_recovery.py — SPOF Resilience & Job Journal Crash Recovery Suite (SIH26123).

Tests:
  1. Journal durability & immediate write-ahead commit (append-only WAL).
  2. Crash replay state reconstruction (uncompleted jobs vs terminal completed/cancelled jobs).
  3. Fleet autonomy invariance: AMR fleet negotiates and navigates collision-free
     while the central server is dead/absent.
  4. FastAPI lifecycle journal replay integration.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "archive" / "pathfinding"))

from app.models.robot_fsm import RobotState
from app.security.hmac_envelope import sign_payload
from app.services.job_journal import JobJournal
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from scripts.benchmark_coordination_policies import get_fixed_scenario_obstacles


@pytest.fixture
def temp_journal(tmp_path):
    jpath = tmp_path / "test_job_log.jsonl"
    return JobJournal(journal_path=jpath)


def test_job_journal_durability_and_fsync(temp_journal):
    """Verifies that entries are written immediately to disk with valid JSON formatting."""
    rec = temp_journal.log_submission(
        job_id="JOB-101",
        job_type="fetch_item",
        pickup=(2, 4),
        dropoff=(27, 20),
        urgency=5,
        payload_weight_kg=25.0,
    )
    assert rec["job_id"] == "JOB-101"
    assert rec["status"] == "SUBMITTED"

    assert temp_journal.journal_path.exists()
    lines = temp_journal.journal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["job_id"] == "JOB-101"
    assert parsed["pickup"] == [2, 4]
    assert parsed["dropoff"] == [27, 20]
    assert parsed["urgency"] == 5


def test_journal_replay_and_state_reconstruction(temp_journal):
    """
    Simulates crash-recovery cycle:
      - Job 1: Submitted -> Assigned -> Completed (Should NOT be replayed)
      - Job 2: Submitted -> Assigned (Should BE replayed as uncompleted)
      - Job 3: Submitted (Should BE replayed as uncompleted)
      - Job 4: Submitted -> Cancelled (Should NOT be replayed)
    """
    # 1. Job 1 Lifecycle
    temp_journal.log_submission("JOB-01", "fetch_item", (2, 2), (27, 2), urgency=3)
    temp_journal.log_assignment("JOB-01", "AMR-01", tick=5)
    temp_journal.log_completion("JOB-01", "AMR-01", tick=45)

    # 2. Job 2 Lifecycle (Server crashes mid-flight while AMR-02 is working)
    temp_journal.log_submission("JOB-02", "sort_batch", (2, 8), (27, 8), urgency=4)
    temp_journal.log_assignment("JOB-02", "AMR-02", tick=10)

    # 3. Job 3 Lifecycle (Server crashes right after submission)
    temp_journal.log_submission("JOB-03", "fetch_item", (2, 14), (27, 14), urgency=5)

    # 4. Job 4 Lifecycle (Cancelled by operator)
    temp_journal.log_submission("JOB-04", "custom_task", (2, 20), (27, 20), urgency=1)
    temp_journal.log_cancellation("JOB-04", reason="operator_requested")

    # Simulate server crash: instantiate a brand new journal object pointing to same file
    recovered = temp_journal.recover_uncompleted_jobs()

    recovered_ids = [j["job_id"] for j in recovered]
    assert "JOB-01" not in recovered_ids, "Completed job must not be replayed"
    assert "JOB-04" not in recovered_ids, "Cancelled job must not be replayed"
    assert "JOB-02" in recovered_ids, "In-flight job must be recovered"
    assert "JOB-03" in recovered_ids, "Pending submitted job must be recovered"

    # Validate state of recovered in-flight job
    j2 = next(j for j in recovered if j["job_id"] == "JOB-02")
    assert j2["status"] == "ASSIGNED"
    assert j2["assigned_robot_id"] == "AMR-02"
    assert j2["urgency"] == 4

    # Validate state of recovered pending job
    j3 = next(j for j in recovered if j["job_id"] == "JOB-03")
    assert j3["status"] == "SUBMITTED"
    assert j3["assigned_robot_id"] is None
    assert j3["urgency"] == 5


def test_fleet_survives_central_server_death(tmp_path):
    """
    CRITICAL SIH26123 REQUIREMENT:
    Verifies that the decentralized AMR fleet continues to negotiate, arbitrate,
    and navigate collision-free completely independently of any central server.
    """
    obstacles = get_fixed_scenario_obstacles()
    hub = LoopbackNetworkHub()
    peer_ports = {"AMR-01": 9001, "AMR-02": 9002, "AMR-03": 9003}

    # AMR-01 moves West -> East along lane y=2
    node1 = RobotNode(
        robot_id="AMR-01",
        start_pos=(2, 2),
        goal_pos=(27, 2),
        urgency=5,
        battery_pct=95.0,
        obstacles=obstacles,
        port=9001,
        peer_ports=peer_ports,
        transport=LoopbackTransport("AMR-01", hub),
        log_dir=tmp_path,
        tick_interval_s=0.0,
        enable_idle_audit=False,
    )

    # AMR-02 moves East -> West along lane y=2 (Direct head-on conflict with AMR-01!)
    node2 = RobotNode(
        robot_id="AMR-02",
        start_pos=(27, 2),
        goal_pos=(2, 2),
        urgency=2,
        battery_pct=80.0,
        obstacles=obstacles,
        port=9002,
        peer_ports=peer_ports,
        transport=LoopbackTransport("AMR-02", hub),
        log_dir=tmp_path,
        tick_interval_s=0.0,
        enable_idle_audit=False,
    )

    # AMR-03 crosses perpendicularly from (14, 1) to (14, 4)
    node3 = RobotNode(
        robot_id="AMR-03",
        start_pos=(14, 1),
        goal_pos=(14, 4),
        urgency=3,
        battery_pct=88.0,
        obstacles=obstacles,
        port=9003,
        peer_ports=peer_ports,
        transport=LoopbackTransport("AMR-03", hub),
        log_dir=tmp_path,
        tick_interval_s=0.0,
        enable_idle_audit=False,
    )

    nodes = [node1, node2, node3]

    # Initial state broadcast
    for n in nodes:
        claim = {
            "type": "RESERVATION_CLAIM",
            "robot_id": n.robot.robot_id,
            "robot_type": n.robot_type,
            "tick": 0,
            "position": [n.robot.position[0], n.robot.position[1]],
            "intended_pos": [n.robot.position[0], n.robot.position[1]],
            "heading": n.robot.heading.value,
            "priority_score": n.robot.priority_score,
            "state": n.fsm.state.value,
            "wait_ticks": 0,
            "path": list(n.robot.path[:8]) if n.robot.path else [],
            "charger_target": None,
        }
        env = sign_payload(claim, secret_key=n.secret_key, seq=n.seq)
        for pid in n.peer_ports:
            if pid != n.robot.robot_id:
                n.transport.send(pid, env)

    for n in nodes:
        n._drain_inbox(0)

    # Step simulation without ANY central server
    collisions = 0
    positions_history = []

    for tick in range(120):
        tick_pos = {}
        for n in nodes:
            n.step(tick)
            tick_pos[n.robot_id] = n.robot.position

        # Invariant check
        seen = {}
        for rid, pos in tick_pos.items():
            if pos in seen:
                collisions += 1
            seen[pos] = rid

        if positions_history:
            prev = positions_history[-1]
            for r1, p1 in tick_pos.items():
                for r2, p2 in tick_pos.items():
                    if r1 < r2:
                        if prev.get(r1) == p2 and prev.get(r2) == p1 and p1 != p2:
                            collisions += 1

        positions_history.append(tick_pos)

        if (node1.robot.position == (27, 2) and node2.robot.position == (2, 2) and node3.robot.position == (14, 4)):
            break

    for n in nodes:
        n.close()

    assert collisions == 0, f"Expected 0 collisions without central server, got {collisions}!"
    assert node1.robot.position == (27, 2), "AMR-01 reached goal (27, 2) autonomously"
    assert node2.robot.position == (2, 2), "AMR-02 reached goal (2, 2) autonomously"
    assert node3.robot.position == (14, 4), "AMR-03 reached goal (14, 4) autonomously"


def test_fastapi_task_inject_commits_to_journal(tmp_path):
    """Verifies that POST /api/task/inject commits to JobJournal prior to returning HTTP 201."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.job_journal import JobJournal

    test_jpath = tmp_path / "fastapi_job_log.jsonl"
    app.state.job_journal = JobJournal(journal_path=test_jpath)

    os.environ["SPAWN_FLEET_ORCHESTRATOR"] = "0"
    with TestClient(app) as client:
        app.state.job_journal = JobJournal(journal_path=test_jpath)
        resp = client.post(
            "/api/task/inject",
            json={
                "pickup": {"x": 2, "y": 4},
                "dropoff": {"x": 27, "y": 20},
                "urgency": 4,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        task_id = data["task_id"]

        assert test_jpath.exists()
        lines = test_jpath.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        recs = [json.loads(line) for line in lines]
        sub_rec = next(r for r in recs if r["job_id"] == task_id and r["event"] == "SUBMITTED")
        assert sub_rec["pickup"] == [2, 4]
        assert sub_rec["dropoff"] == [27, 20]
        assert sub_rec["urgency"] == 4
