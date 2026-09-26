"""
NET-01 Verification Suite: Proximity Filtering & Full-Mesh Safety Guarantees

Verifies:
1. Routine tick heartbeat broadcasts drop packet volume by suppressing sends to distant peers outside proximity_radius.
2. Routine packets to nearby peers (within proximity_radius) are transmitted normally.
3. Safety-critical envelopes (RESOURCE_CLAIM, RESOURCE_RELEASE, POD_SLOT_OCCUPANCY) bypass the proximity filter
   and reach 100% of all mesh nodes regardless of distance.
4. Proximity radius is fully configurable (can be tuned or disabled).
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app"))

import pytest
from app.services.robot_node import RobotNode
from app.transport.udp_transport import UdpTransport
from app.models.world import build_default_world


@pytest.fixture
def clean_reservations():
    from app.services.reservations import clear_all_claims
    clear_all_claims()
    yield
    clear_all_claims()


def test_proximity_filter_reduces_routine_broadcast_volume(clean_reservations):
    """
    AMR-01 at (2, 2) has:
    - AMR-02 at (4, 4) -> distance = ~2.8m (within 10m proximity)
    - AMR-03 at (25, 25) -> distance = ~32.5m (outside 10m proximity)

    Verifies:
    - Initial tick enables discovery.
    - Subsequent tick filters routine state to AMR-03 while sending to AMR-02.
    - packets_filtered_count increments, proving packet volume drops.
    """
    peer_ports = {
        "AMR-01": 9851,
        "AMR-02": 9852,
        "AMR-03": 9853,
    }
    t1 = UdpTransport("AMR-01", 9851, peer_ports)
    t2 = UdpTransport("AMR-02", 9852, peer_ports)
    t3 = UdpTransport("AMR-03", 9853, peer_ports)
    world = build_default_world()

    # Configure proximity_radius=10.0
    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t1, peer_ports=peer_ports, proximity_radius=10.0, obstacles=world.static_obstacles)
    bot2 = RobotNode("AMR-02", (4, 4), robot_type="GOODS_TO_PERSON", transport=t2, peer_ports=peer_ports, proximity_radius=10.0, obstacles=world.static_obstacles)
    bot3 = RobotNode("AMR-03", (25, 25), robot_type="GOODS_TO_PERSON", transport=t3, peer_ports=peer_ports, proximity_radius=10.0, obstacles=world.static_obstacles)

    try:
        from app.models.robot import Heading, RobotState
        from app.services.robot_node import PeerSnapshot

        # Pre-seed peer positions (as learned from prior discovery or network roster)
        bot1.peers["AMR-02"] = PeerSnapshot(
            robot_id="AMR-02",
            position=(4, 4),
            intended_pos=(4, 4),
            heading=Heading.NORTH,
            priority_score=0.0,
            state=RobotState.IDLE,
            wait_ticks_so_far=0,
            path=[],
            last_seen_tick=1,
            charger_target=None,
            robot_type="GOODS_TO_PERSON",
            occupied_slot=None,
        )
        bot1.peers["AMR-03"] = PeerSnapshot(
            robot_id="AMR-03",
            position=(25, 25),
            intended_pos=(25, 25),
            heading=Heading.NORTH,
            priority_score=0.0,
            state=RobotState.IDLE,
            wait_ticks_so_far=0,
            path=[],
            last_seen_tick=1,
            charger_target=None,
            robot_type="GOODS_TO_PERSON",
            occupied_slot=None,
        )

        # Confirm proximity filter evaluation:
        # AMR-02 (~2.8m) is in proximity; AMR-03 (~32.5m) is NOT
        assert bot1.is_peer_in_proximity("AMR-02") is True
        assert bot1.is_peer_in_proximity("AMR-03") is False

        # Reset counters to measure tick 2 behavior
        bot1.packets_sent_count = 0
        bot1.packets_filtered_count = 0

        # Tick 2: bot1 executes step()
        bot1.step(2)

        # In tick 2, bot1 had 2 peers (AMR-02, AMR-03):
        # - AMR-02 (within proximity) was SENT
        # - AMR-03 (outside proximity) was FILTERED
        assert bot1.packets_sent_count == 1
        assert bot1.packets_filtered_count == 1

        # Drain inboxes: AMR-02 received the heartbeat; AMR-03 did not
        bot2.step(2)
        bot3.step(2)
        assert bot2.peers["AMR-01"].last_seen_tick == 2
        # AMR-03 was NOT sent the tick 2 heartbeat
        assert "AMR-01" not in bot3.peers or bot3.peers["AMR-01"].last_seen_tick < 2
    finally:
        bot1.close()
        bot2.close()
        bot3.close()


def test_safety_critical_claims_bypass_proximity_filter(clean_reservations):
    """
    Safety-critical claims (RESOURCE_CLAIM) MUST reach 100% full mesh regardless of proximity.
    AMR-01 claims a pod; distant AMR-03 (32.5m away, well outside 10m radius) MUST receive it.
    """
    peer_ports = {
        "AMR-01": 9861,
        "AMR-02": 9862,
        "AMR-03": 9863,
    }
    t1 = UdpTransport("AMR-01", 9861, peer_ports)
    t2 = UdpTransport("AMR-02", 9862, peer_ports)
    t3 = UdpTransport("AMR-03", 9863, peer_ports)
    world = build_default_world()

    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t1, peer_ports=peer_ports, proximity_radius=10.0, obstacles=world.static_obstacles)
    bot2 = RobotNode("AMR-02", (4, 4), robot_type="GOODS_TO_PERSON", transport=t2, peer_ports=peer_ports, proximity_radius=10.0, obstacles=world.static_obstacles)
    bot3 = RobotNode("AMR-03", (25, 25), robot_type="GOODS_TO_PERSON", transport=t3, peer_ports=peer_ports, proximity_radius=10.0, obstacles=world.static_obstacles)

    try:
        # Tick 1: Discovery
        bot1.step(1)
        bot2.step(1)
        bot3.step(1)

        # AMR-01 claims pod resource POD-TEST-42
        bot1.claim_pod_resource(shelf_id="POD-TEST-42", current_tick=2, lease_ticks=40)

        # Distant AMR-03 (outside proximity radius) steps and drains inbox
        bot3.step(2)

        # Distant AMR-03 MUST have received the claim and recorded it in reservations!
        from app.services.reservations import get_pod_claim
        claim_on_bot3 = get_pod_claim("POD-TEST-42")
        assert claim_on_bot3 == "AMR-01"

        # Now test RESOURCE_RELEASE full mesh reach: AMR-01 releases the pod
        bot1.broadcast_resource_release("POD", "POD-TEST-42", current_tick=3)
        bot3.step(3)

        claim_after_release = get_pod_claim("POD-TEST-42")
        assert claim_after_release is None
    finally:
        bot1.close()
        bot2.close()
        bot3.close()


def test_safety_critical_pod_slot_occupancy_bypasses_proximity_filter(clean_reservations):
    """
    Grid pod-slot occupancy updates (POD_SLOT_OCCUPANCY) MUST reach all peers,
    preventing pathfinders anywhere in the warehouse from planning through occupied slots.
    """
    peer_ports = {
        "AMR-01": 9871,
        "AMR-03": 9873,
    }
    t1 = UdpTransport("AMR-01", 9871, peer_ports)
    t3 = UdpTransport("AMR-03", 9873, peer_ports)
    world = build_default_world()

    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t1, peer_ports=peer_ports, proximity_radius=8.0, obstacles=world.static_obstacles)
    bot3 = RobotNode("AMR-03", (25, 25), robot_type="GOODS_TO_PERSON", transport=t3, peer_ports=peer_ports, proximity_radius=8.0, obstacles=world.static_obstacles)

    try:
        # Discovery
        bot1.step(1)
        bot3.step(1)

        # AMR-01 updates pod slot occupancy at (10, 10)
        bot1.set_pod_slot_occupant(pos=(10, 10), occupant="AMR-01", shelf_id="POD-A01", tick=2)

        # Distant bot3 steps
        bot3.step(2)

        # Distant bot3's local grid MUST have updated pod slot occupant
        assert bot3.grid.pod_slot_occupants.get((10, 10)) == "AMR-01"
    finally:
        bot1.close()
        bot3.close()


def test_proximity_radius_configuration_toggle(clean_reservations):
    """Verifies proximity filter can be configured or disabled."""
    peer_ports = {"AMR-01": 9881, "AMR-02": 9882}
    t1 = UdpTransport("AMR-01", 9881, peer_ports)
    t2 = UdpTransport("AMR-02", 9882, peer_ports)
    world = build_default_world()

    # Disabled proximity filter (proximity_radius=None)
    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t1, peer_ports=peer_ports, proximity_radius=None, obstacles=world.static_obstacles)
    bot2 = RobotNode("AMR-02", (28, 28), robot_type="GOODS_TO_PERSON", transport=t2, peer_ports=peer_ports, proximity_radius=None, obstacles=world.static_obstacles)

    try:
        bot1.step(1)
        bot2.step(1)

        # Even though distance is ~36m, with proximity_radius=None, is_peer_in_proximity is True
        assert bot1.is_peer_in_proximity("AMR-02") is True

        bot1.packets_filtered_count = 0
        bot1.packets_sent_count = 0
        bot1.step(2)

        # Full mesh: 1 sent, 0 filtered
        assert bot1.packets_sent_count == 1
        assert bot1.packets_filtered_count == 0
    finally:
        bot1.close()
        bot2.close()
