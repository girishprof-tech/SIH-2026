"""
ARCH-01 Verification Suite: Lightweight AMR Interim Coordinator Election & Fault Escalation

Verifies:
1. AuthorityStation broadcasts signed STATION_HEARTBEAT periodically; AMRs track authority_last_seen_tick.
2. When AuthorityStation misses >= 5 consecutive ticks, AMRs deterministically elect an Interim Coordinator
   using (priority_score, robot_id) tie-breaking.
3. Only the elected Interim Coordinator approves escalated faults from domain stations (Import/Export).
4. When AuthorityStation revives and sends a heartbeat, the Interim Coordinator immediately steps down.
5. Zero Split-Brain invariant: never 2 active authorities at any time, clean handback.
"""

import sys
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app"))

import pytest
from app.services.station_node import StationNode, StationRole
from app.services.robot_node import RobotNode
from app.transport.udp_transport import UdpTransport
from app.models.world import build_default_world
from app.models.task import Task, TaskType


@pytest.fixture
def clean_reservations():
    from app.services.reservations import clear_all_claims
    clear_all_claims()
    yield
    clear_all_claims()


def test_authority_station_heartbeat_broadcast(clean_reservations):
    """AuthorityStation emits signed STATION_HEARTBEAT and AMRs keep authority_last_seen_tick fresh."""
    peer_ports = {
        "AMR-01": 9801,
        "AUTHORITY_STATION": 9803,
    }
    t_amr = UdpTransport("AMR-01", 9801, peer_ports)
    world = build_default_world()
    bot = RobotNode("AMR-01", (5, 5), robot_type="GOODS_TO_PERSON", transport=t_amr, peer_ports=peer_ports, obstacles=world.static_obstacles)

    auth = StationNode("AUTHORITY_STATION", StationRole.AUTHORITY_STATION, (15, 14), 9803, peer_ports)

    try:
        # Before any tick, authority_last_seen_tick is 0 and bot is not interim coordinator
        assert bot.authority_last_seen_tick == 0
        assert bot.is_interim_coordinator is False

        # Step 1: Authority Station ticks and broadcasts heartbeat
        auth.poll_and_step(current_tick=1)
        # AMR-01 steps and drains inbox
        bot.step(tick=1)

        # AMR-01 received the heartbeat
        assert bot.authority_last_seen_tick == 1
        assert bot.is_interim_coordinator is False
        assert bot.interim_coordinator_id is None

        # Step 2: Authority Station ticks again
        auth.poll_and_step(current_tick=2)
        bot.step(tick=2)
        assert bot.authority_last_seen_tick == 2
        assert bot.is_interim_coordinator is False
    finally:
        bot.close()
        auth.close()


def test_interim_coordinator_election_on_authority_timeout(clean_reservations):
    """When AuthorityStation is dead for >= 5 ticks, AMRs deterministically elect Interim Coordinator."""
    peer_ports = {
        "AMR-01": 9811,
        "AMR-02": 9812,
        "AUTHORITY_STATION": 9813,
    }
    t_amr1 = UdpTransport("AMR-01", 9811, peer_ports)
    t_amr2 = UdpTransport("AMR-02", 9812, peer_ports)
    world = build_default_world()

    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t_amr1, peer_ports=peer_ports, obstacles=world.static_obstacles)
    bot2 = RobotNode("AMR-02", (2, 4), robot_type="GOODS_TO_PERSON", transport=t_amr2, peer_ports=peer_ports, obstacles=world.static_obstacles)

    try:
        # Authority Station is NOT running.
        # Run ticks 1..4: Timeout threshold (5 ticks) not yet reached
        for tick in range(1, 5):
            bot1.step(tick)
            bot2.step(tick)
            assert bot1.is_interim_coordinator is False
            assert bot2.is_interim_coordinator is False

        # Tick 5: Timeout threshold reached (tick 5 - 0 >= 5)
        bot1.step(5)
        bot2.step(5)

        # Both have priority_score 0.0 -> tie-breaker picks lowest robot_id string ("AMR-01" < "AMR-02")
        assert bot1.is_interim_coordinator is True
        assert bot1.interim_coordinator_id == "AMR-01"

        assert bot2.is_interim_coordinator is False
        assert bot2.interim_coordinator_id == "AMR-01"

        # Exactly ONE coordinator elected across the mesh
        total_coordinators = sum([bot1.is_interim_coordinator, bot2.is_interim_coordinator])
        assert total_coordinators == 1

        # Now test priority score override: if AMR-02 gets active high-urgency task, election shifts deterministically
        bot2._assign_initial_task(
            goal_pos=(4, 6),
            pickup_pos=(2, 4),
            urgency=5,
            task_id="TASK-PRIORITY-BOOST",
        )
        bot1.step(6)
        bot2.step(6)
        bot1.step(7)
        bot2.step(7)

        assert bot2.is_interim_coordinator is True
        assert bot2.interim_coordinator_id == "AMR-02"
        assert bot1.is_interim_coordinator is False
        assert bot1.interim_coordinator_id == "AMR-02"

        total_coordinators = sum([bot1.is_interim_coordinator, bot2.is_interim_coordinator])
        assert total_coordinators == 1
    finally:
        bot1.close()
        bot2.close()


def test_interim_coordinator_fault_escalation_resolution(clean_reservations):
    """ImportStation escalates fault to mesh; only elected Interim Coordinator approves it."""
    peer_ports = {
        "AMR-01": 9821,
        "AMR-02": 9822,
        "IMPORT_STATION": 9824,
        "AUTHORITY_STATION": 9823,
    }
    t_amr1 = UdpTransport("AMR-01", 9821, peer_ports)
    t_amr2 = UdpTransport("AMR-02", 9822, peer_ports)
    world = build_default_world()

    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t_amr1, peer_ports=peer_ports, obstacles=world.static_obstacles)
    bot2 = RobotNode("AMR-02", (2, 4), robot_type="GOODS_TO_PERSON", transport=t_amr2, peer_ports=peer_ports, obstacles=world.static_obstacles)

    import_station = StationNode("IMPORT_STATION", StationRole.IMPORT_STATION, (1, 14), 9824, peer_ports)
    import_station.is_authority_online = False

    try:
        # Advance 5 ticks so AMR-01 becomes Interim Coordinator
        for tick in range(1, 6):
            bot1.step(tick)
            bot2.step(tick)
            import_station.poll_and_step(tick)

        assert bot1.is_interim_coordinator is True
        assert bot2.is_interim_coordinator is False

        # ImportStation escalates fault with fallback_to_interim=True
        esc_res = import_station.escalate_fault_to_interim(
            fault_id="FAULT-CONVEYOR-JAM-01",
            description="Conveyor motor jam at inbound dock IN-1",
            severity="HIGH",
        )
        assert esc_res["status"] == "ESCALATED_TO_INTERIM_COORDINATOR"
        assert esc_res["escalated"] is True

        # AMRs process the next tick
        bot1.step(6)
        bot2.step(6)

        # Interim Coordinator (AMR-01) approved the escalated fault
        assert len(bot1.resolved_escalated_faults) == 1
        res_record = bot1.resolved_escalated_faults[0]
        assert res_record["fault_id"] == "FAULT-CONVEYOR-JAM-01"
        assert res_record["status"] == "APPROVED_BY_INTERIM_COORDINATOR"
        assert res_record["resolved_by"] == "AMR-01"

        # Non-coordinator (AMR-02) stood by and did not resolve it
        assert len(bot2.resolved_escalated_faults) == 0
    finally:
        bot1.close()
        bot2.close()
        import_station.close()


def test_authority_station_revival_clean_handback_zero_split_brain(clean_reservations):
    """
    When AuthorityStation is killed and revived:
    - Interim coordinator is elected on timeout.
    - The instant AuthorityStation revives and sends a heartbeat, interim coordinator steps down.
    - Verifies zero split-brain: never 2 active authorities, never 0 after timeout.
    """
    peer_ports = {
        "AMR-01": 9831,
        "AMR-02": 9832,
        "AUTHORITY_STATION": 9833,
    }
    t_amr1 = UdpTransport("AMR-01", 9831, peer_ports)
    t_amr2 = UdpTransport("AMR-02", 9832, peer_ports)
    world = build_default_world()

    bot1 = RobotNode("AMR-01", (2, 2), robot_type="GOODS_TO_PERSON", transport=t_amr1, peer_ports=peer_ports, obstacles=world.static_obstacles)
    bot2 = RobotNode("AMR-02", (2, 4), robot_type="GOODS_TO_PERSON", transport=t_amr2, peer_ports=peer_ports, obstacles=world.static_obstacles)

    auth = StationNode("AUTHORITY_STATION", StationRole.AUTHORITY_STATION, (15, 14), 9833, peer_ports)

    try:
        # Phase 1: Authority is alive for ticks 1..3
        for tick in range(1, 4):
            auth.poll_and_step(tick)
            bot1.step(tick)
            bot2.step(tick)
            assert bot1.is_interim_coordinator is False
            assert bot2.is_interim_coordinator is False
            # Active authority: exactly AuthorityStation

        # Phase 2: Authority Station is killed / silenced at tick 4
        auth.is_authority_online = False

        # Ticks 4..8: 5 consecutive missed heartbeats (last seen was tick 3)
        for tick in range(4, 9):
            bot1.step(tick)
            bot2.step(tick)

        # At tick 8: 8 - 3 = 5 >= 5 -> Interim coordinator elected
        assert bot1.is_interim_coordinator is True
        assert bot2.is_interim_coordinator is False
        # Active authority: exactly AMR-01 (1 active authority)

        # Phase 3: Authority Station revives at tick 9!
        auth.is_authority_online = True
        auth.poll_and_step(current_tick=9)  # Emits STATION_HEARTBEAT

        # AMRs process tick 9
        bot1.step(tick=9)
        bot2.step(tick=9)

        # Interim coordinator MUST have stepped down immediately
        assert bot1.is_interim_coordinator is False
        assert bot1.interim_coordinator_id is None
        assert bot2.is_interim_coordinator is False
        assert bot2.interim_coordinator_id is None
        assert bot1.authority_last_seen_tick == 9
        assert bot2.authority_last_seen_tick == 9

        # Zero split-brain confirmed: active authority returned cleanly to AuthorityStation
        # Continue for 3 more ticks with Authority alive
        for tick in range(10, 13):
            auth.poll_and_step(tick)
            bot1.step(tick)
            bot2.step(tick)
            assert bot1.is_interim_coordinator is False
            assert bot2.is_interim_coordinator is False
            assert bot1.authority_last_seen_tick == tick
    finally:
        bot1.close()
        bot2.close()
        auth.close()
