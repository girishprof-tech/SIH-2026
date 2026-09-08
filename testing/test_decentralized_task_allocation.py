"""
test_decentralized_task_allocation.py — Contract-Net Decentralized Task Allocation Tests.

Verifies:
  1. Signed TASK_ANNOUNCEMENT broadcast over UDP / Transport.
  2. Independent bid calculation using distance + battery penalty.
  3. Peer-to-peer bid collection, resolution, and tie-breaking.
  4. Exactly ONE claiming robot per job (no double claims, zero orphaned jobs).
  5. Winning bidder is among the closest idle robots at announcement time.
  6. Negotiation and assignment survive central server death (FastAPI absent).
"""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotState
from app.models.task import Task, TaskStatus
from app.services.robot_node import RobotNode
from app.services.task_manager import TaskManager, build_task_announcement_envelope
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from grid import WarehouseGrid
from models import Heading, Robot


def create_in_memory_fleet(
    num_robots: int = 5,
    starts: Optional[List[Tuple[int, int]]] = None,
) -> Tuple[List[RobotNode], LoopbackNetworkHub]:
    """Creates a deterministic in-memory fleet of RobotNodes over a shared LoopbackNetworkHub."""
    hub = LoopbackNetworkHub()
    peer_ports = {f"AMR-{i+1:02d}": 9000 + i + 1 for i in range(num_robots)}
    default_starts = [(2, 4), (2, 9), (2, 15), (2, 24), (27, 4), (27, 12)]
    start_positions = starts or default_starts[:num_robots]

    nodes: List[RobotNode] = []
    temp_dir = ROOT_DIR / "logs" / "test_contract_net"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for i in range(num_robots):
        rid = f"AMR-{i+1:02d}"
        transport = LoopbackTransport(node_id=rid, hub=hub)
        node = RobotNode(
            robot_id=rid,
            start_pos=start_positions[i],
            goal_pos=None,
            urgency=1,
            battery_pct=90.0 - (i * 5.0),
            obstacles=[],
            port=peer_ports[rid],
            peer_ports=peer_ports,
            transport=transport,
            log_dir=temp_dir,
            tick_interval_s=0.0,
            robot_type="GOODS_TO_PERSON",
            enable_idle_audit=False,
        )
        nodes.append(node)

    return nodes, hub


def test_contract_net_bidding_and_uniqueness():
    """
    Asserts:
      (a) Exactly ONE robot claims each task (no double claims, no duplicate claims).
      (b) The claiming robot is among the closest 2 idle robots to the pickup location.
    """
    # 5 robots at (2, 4), (2, 9), (2, 15), (2, 24), (27, 4)
    nodes, hub = create_in_memory_fleet(5)

    # Inject task with pickup at (3, 5) — closest should be AMR-01 (at 2, 4, dist=2)
    task1 = Task(
        task_id="TASK-CNET-01",
        pickup_x=3,
        pickup_y=5,
        dropoff_x=20,
        dropoff_y=20,
        urgency=4,
        created_tick=0,
        status=TaskStatus.PENDING,
    )

    announcement = build_task_announcement_envelope(task1)

    # Dispatcher broadcasts to all 5 robots
    for node in nodes:
        hub.deliver(node.robot_id, announcement)

    # Tick 0: Robots drain announcement, calculate bids, broadcast TASK_BID
    for node in nodes:
        node.step(tick=0)

    # Verify all 5 robots recorded their own bid
    for node in nodes:
        assert "TASK-CNET-01" in node.active_bids
        assert node.active_bids["TASK-CNET-01"]["my_bid"] > 0

    # Tick 1: Robots receive peer bids
    for node in nodes:
        node.step(tick=1)

    # Tick 2: 2-tick window elapses, bids resolve, winner self-assigns and broadcasts TASK_CLAIM
    for node in nodes:
        node.step(tick=2)

    # Tick 3: Peers drain TASK_CLAIM and stand down
    for node in nodes:
        node.step(tick=3)

    # Assertions
    # (a) Exactly one robot claimed the task
    claiming_nodes = [node for node in nodes if node.task and node.task.task_id == "TASK-CNET-01"]
    assert len(claiming_nodes) == 1, f"Expected exactly 1 claim, found {len(claiming_nodes)}"

    winner = claiming_nodes[0]
    # (b) AMR-01 is closest to (3, 5), so AMR-01 should win
    assert winner.robot_id in ("AMR-01", "AMR-02"), f"Expected winner among closest 2 robots, got {winner.robot_id}"
    assert winner.robot_id == "AMR-01"

    # Verify all other nodes stood down and have zero active task
    for node in nodes:
        if node.robot_id != winner.robot_id:
            assert node.task is None
            assert node.fsm.state == RobotState.IDLE

    # Clean up
    for node in nodes:
        node.close()


def test_contract_net_tie_breaking():
    """
    Verifies that when two robots have IDENTICAL bid scores,
    the deterministic tie-breaker (lowest robot_id string) wins.
    """
    # 2 robots equidistant to pickup (5, 5)
    # AMR-01 at (4, 5) [dist 1], AMR-02 at (6, 5) [dist 1], identical battery (80.0)
    starts = [(4, 5), (6, 5)]
    nodes, hub = create_in_memory_fleet(2, starts=starts)
    nodes[0].robot.battery_pct = 80.0
    nodes[1].robot.battery_pct = 80.0

    task = Task(
        task_id="TASK-TIE-01",
        pickup_x=5,
        pickup_y=5,
        dropoff_x=25,
        dropoff_y=25,
        urgency=3,
        created_tick=0,
    )
    announcement = build_task_announcement_envelope(task)
    for node in nodes:
        hub.deliver(node.robot_id, announcement)

    # Run 3 ticks to complete contract-net bidding
    for tick in range(4):
        for node in nodes:
            node.step(tick=tick)

    # AMR-01 has lower lexicographical string ("AMR-01" < "AMR-02")
    assert nodes[0].task is not None and nodes[0].task.task_id == "TASK-TIE-01"
    assert nodes[1].task is None

    for node in nodes:
        node.close()


def test_assignment_survives_central_server_death():
    """
    Verifies that once TASK_ANNOUNCEMENT is broadcast, the central server
    (FastAPI process / TaskManager) can be completely dead / non-existent,
    and robots complete the bid and claim negotiation purely peer-to-peer.
    """
    nodes, hub = create_in_memory_fleet(3)

    task = Task(
        task_id="TASK-NO-SERVER",
        pickup_x=2,
        pickup_y=10,
        dropoff_x=20,
        dropoff_y=20,
        urgency=5,
        created_tick=0,
    )

    # Server broadcasts announcement and then dies (no more server interactions)
    announcement = build_task_announcement_envelope(task)
    for node in nodes:
        hub.deliver(node.robot_id, announcement)

    # Server is DEAD: Only autonomous robot nodes step and communicate peer-to-peer
    for tick in range(4):
        for node in nodes:
            node.step(tick=tick)

    # Confirm negotiation finished and task was claimed without central server
    claimed = [n for n in nodes if n.task and n.task.task_id == "TASK-NO-SERVER"]
    assert len(claimed) == 1, "Task negotiation must complete peer-to-peer without central server"
    # AMR-02 is at (2, 9), distance to (2, 10) is 1 cell -> AMR-02 wins
    assert claimed[0].robot_id == "AMR-02"

    for node in nodes:
        node.close()


def test_concurrent_multi_task_allocation():
    """
    Injects 5 concurrent tasks across a 6-robot fleet.
    Asserts zero double-claims and all tasks mapped 1-to-1 to distinct robots.
    """
    starts = [(2, 4), (2, 9), (2, 15), (2, 24), (27, 4), (27, 12)]
    nodes, hub = create_in_memory_fleet(6, starts=starts)

    tasks = [
        Task(task_id=f"TASK-MULTI-{i}", pickup_x=starts[i][0] + 1, pickup_y=starts[i][1], dropoff_x=15, dropoff_y=15, urgency=3, created_tick=0)
        for i in range(5)
    ]

    # Broadcast all 5 tasks concurrently
    for task in tasks:
        envelope = build_task_announcement_envelope(task)
        for node in nodes:
            hub.deliver(node.robot_id, envelope)

    # Run simulation ticks
    for tick in range(6):
        for node in nodes:
            node.step(tick=tick)

    # Check claims
    claimed_task_ids = set()
    assigned_nodes = []
    for node in nodes:
        if node.task:
            tid = node.task.task_id
            assert tid not in claimed_task_ids, f"Duplicate claim detected for task {tid}!"
            claimed_task_ids.add(tid)
            assigned_nodes.append(node.robot_id)

    # Assert 5 distinct tasks claimed by 5 distinct robots
    assert len(claimed_task_ids) == 5, f"Expected 5 claimed tasks, got {len(claimed_task_ids)}"
    assert len(set(assigned_nodes)) == 5, "Expected 5 distinct robots assigned"

    for node in nodes:
        node.close()
