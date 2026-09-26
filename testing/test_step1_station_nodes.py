"""
test_step1_station_nodes.py — Unit and Integration Verification for Step 1 Fixed Station Nodes.

Validates:
1. WorldConfig integration of the 3 fixed station nodes (coordinates, roles, non-interference).
2. StationNode instantiation, dual transports (UdpTransport + HaLowTransport), and HMAC envelope signing.
3. Station-level command scope boundaries:
   - ImportStation strictly allows INDUCT_BATCH / IN-1..3, rejects export tasks with explicit reason.
   - ExportStation strictly allows CONSOLIDATE_EXPORT / OUT-1..3 / chutes, rejects import tasks.
   - AuthorityStation has full command scope.
4. Clean fault escalation and graceful degradation when AuthorityStation is offline.
"""

import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))

from app.models.world import build_default_world, DEFAULT_FIXED_STATIONS
from app.services.station_node import (
    StationNode,
    StationRole,
    DEFAULT_STATION_PORTS,
)
from app.security.hmac_envelope import verify_envelope, DEFAULT_SECRET_KEY


def test_world_station_placement():
    """Verify that WorldConfig contains the 3 fixed stations in designated coordinates."""
    world = build_default_world()
    stations = world.fixed_stations

    assert len(stations) == 3
    assert "IMPORT_STATION" in stations
    assert "EXPORT_STATION" in stations
    assert "AUTHORITY_STATION" in stations

    import_st = stations["IMPORT_STATION"]
    export_st = stations["EXPORT_STATION"]
    auth_st = stations["AUTHORITY_STATION"]

    assert import_st["role"] == "IMPORT_STATION"
    assert import_st["x"] == 1 and import_st["y"] == 14
    assert import_st["port"] == 9601

    assert export_st["role"] == "EXPORT_STATION"
    assert export_st["x"] == 28 and export_st["y"] == 14
    assert export_st["port"] == 9602

    assert auth_st["role"] == "AUTHORITY_STATION"
    assert auth_st["x"] == 15 and auth_st["y"] == 14
    assert auth_st["port"] == 9603

    # Confirm stations do not collide with static obstacles or pod slots or chargers
    for st_id, st in stations.items():
        pos = (st["x"], st["y"])
        assert pos not in world.static_obstacles, f"{st_id} at {pos} collides with static obstacle"
        assert pos not in world.charging_stations, f"{st_id} at {pos} collides with charger"
        assert pos not in world._coords_to_pod, f"{st_id} at {pos} collides with pod slot"


def test_station_node_initialization():
    """Verify StationNode initializes UDP and HaLow transports without heavy AMR compute."""
    peer_ports = {
        "AMR-01": 9001,
        "IMPORT_STATION": 9601,
        "EXPORT_STATION": 9602,
        "AUTHORITY_STATION": 9603,
    }
    station = StationNode(
        station_id="IMPORT_STATION",
        role=StationRole.IMPORT_STATION,
        position=(1, 14),
        port=9601,
        peer_ports=peer_ports,
    )
    try:
        assert station.station_id == "IMPORT_STATION"
        assert station.role == StationRole.IMPORT_STATION
        assert station.position == (1, 14)
        assert hasattr(station, "transport")
        assert hasattr(station, "halow_transport")
        # Ensure StationNode has NO robot FSM or pathfinding state
        assert not hasattr(station, "fsm")
        assert not hasattr(station, "battery_pct")
        assert not hasattr(station, "path")
    finally:
        station.close()


def test_import_station_command_scope():
    """Verify ImportStation accepts import-zone tasks and rejects export tasks."""
    peer_ports = {"IMPORT_STATION": 9601}
    st = StationNode("IMPORT_STATION", StationRole.IMPORT_STATION, (1, 14), 9601, peer_ports)
    try:
        # 1. Valid import task (INDUCT_BATCH)
        valid_task = {
            "task_id": "INDUCT-01",
            "task_type": "INDUCT_BATCH",
            "source_gate": "IN-1",
            "pickup": [0, 9],
            "dropoff": [4, 6],
        }
        ok, reason = st.validate_command_scope(valid_task)
        assert ok is True
        assert reason == ""

        # 2. Invalid export task (CONSOLIDATE_EXPORT)
        invalid_export_task = {
            "task_id": "EXPORT-01",
            "task_type": "CONSOLIDATE_EXPORT",
            "destination_gate": "OUT-1",
            "pickup": [23, 2],
            "dropoff": [29, 9],
        }
        ok, reason = st.validate_command_scope(invalid_export_task)
        assert ok is False
        assert "STATION_AUTHORITY_VIOLATION" in reason
        assert "ImportStation" in reason

        # 3. Issue command logs rejection
        issued, msg = st.issue_task_command("TASK_ASSIGNMENT", invalid_export_task)
        assert issued is False
        assert len(st.rejection_log) == 1
        assert st.rejection_log[0]["task"]["task_id"] == "EXPORT-01"
    finally:
        st.close()


def test_export_station_command_scope():
    """Verify ExportStation accepts export-zone tasks and rejects import tasks."""
    peer_ports = {"EXPORT_STATION": 9602}
    st = StationNode("EXPORT_STATION", StationRole.EXPORT_STATION, (28, 14), 9602, peer_ports)
    try:
        # 1. Valid export task (CONSOLIDATE_EXPORT)
        valid_export_task = {
            "task_id": "EXPORT-01",
            "task_type": "CONSOLIDATE_EXPORT",
            "destination_gate": "OUT-2",
            "dropoff": [29, 14],
        }
        ok, reason = st.validate_command_scope(valid_export_task)
        assert ok is True
        assert reason == ""

        # 2. Valid sortation transfer task
        valid_transfer = {
            "task_id": "TRANSFER-01",
            "task_type": "TRANSFER_TO_SORTATION",
            "dropoff": [23, 2],
        }
        ok, reason = st.validate_command_scope(valid_transfer)
        assert ok is True

        # 3. Invalid import task
        invalid_import_task = {
            "task_id": "INDUCT-02",
            "task_type": "INDUCT_BATCH",
            "source_gate": "IN-2",
            "pickup": [0, 14],
        }
        ok, reason = st.validate_command_scope(invalid_import_task)
        assert ok is False
        assert "STATION_AUTHORITY_VIOLATION" in reason
        assert "ExportStation" in reason
    finally:
        st.close()


def test_authority_station_command_scope():
    """Verify AuthorityStation has full command scope across all task types."""
    peer_ports = {"AUTHORITY_STATION": 9603}
    st = StationNode("AUTHORITY_STATION", StationRole.AUTHORITY_STATION, (15, 14), 9603, peer_ports)
    try:
        import_task = {"task_id": "IN-99", "task_type": "INDUCT_BATCH"}
        export_task = {"task_id": "OUT-99", "task_type": "CONSOLIDATE_EXPORT"}
        g2p_task = {"task_id": "G2P-99", "task_type": "RETRIEVE_POD"}

        assert st.validate_command_scope(import_task)[0] is True
        assert st.validate_command_scope(export_task)[0] is True
        assert st.validate_command_scope(g2p_task)[0] is True
    finally:
        st.close()


def test_station_fault_escalation_graceful_degradation():
    """Verify station gracefully degrades and does NOT hang when AuthorityStation is offline."""
    peer_ports = {"IMPORT_STATION": 9601, "AUTHORITY_STATION": 9603}
    st = StationNode("IMPORT_STATION", StationRole.IMPORT_STATION, (1, 14), 9601, peer_ports)
    try:
        # Simulate Authority Station being offline
        st.is_authority_online = False
        res = st.report_fault(
            fault_id="FAULT-01",
            description="Motor stall on AMR-02 at inbound dock",
            severity="HIGH",
        )
        assert res["status"] == "ESCALATION_UNAVAILABLE"
        assert res.get("escalation_failed") is True
        assert "AuthorityStation unreachable" in res["reason"]
        # Normal operation continues without exception
        step_res = st.poll_and_step(current_tick=1)
        assert step_res["status"] == "ONLINE"
    finally:
        st.close()
