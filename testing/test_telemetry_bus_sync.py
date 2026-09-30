import multiprocessing as mp
import time
import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.services.telemetry_bus import TelemetryBus, reset_telemetry_cache


def test_telemetry_bus_synchronizes_multi_robot_ticks():
    reset_telemetry_cache()
    q = mp.Queue()
    bus = TelemetryBus(q, fleet_size=3)

    # Robot 1 sends tick 1 first
    q.put({
        "robot_id": "G2P-01",
        "tick": 1,
        "x": 5, "y": 6,
        "heading": "NORTH",
        "state": "EN_ROUTE_PICKUP",
        "battery_pct": 99.0,
        "priority_score": 10.0,
        "wait_ticks": 0,
        "action": "MOVED",
        "completed": False,
        "path": [{"x": 5, "y": 7, "t": 2}],
        "goal": [5, 10],
    })

    # Draining while only 1 out of 3 robots reported tick 1
    # Should NOT advance current_tick to 1 yet!
    bus.process_incoming()
    assert bus.current_tick == 0, f"Expected current_tick 0 until quorum, got {bus.current_tick}"

    # Robot 2 sends tick 1
    q.put({
        "robot_id": "G2P-02",
        "tick": 1,
        "x": 10, "y": 12,
        "heading": "SOUTH",
        "state": "IDLE",
        "battery_pct": 100.0,
        "priority_score": 5.0,
        "wait_ticks": 0,
        "action": "IDLE",
        "completed": True,
        "path": [],
        "goal": [10, 12],
    })
    bus.process_incoming()
    assert bus.current_tick == 0

    # Robot 3 sends tick 1 (quorum reached!)
    q.put({
        "robot_id": "G2P-03",
        "tick": 1,
        "x": 15, "y": 20,
        "heading": "EAST",
        "state": "EN_ROUTE_DROPOFF",
        "battery_pct": 98.0,
        "priority_score": 15.0,
        "wait_ticks": 0,
        "action": "MOVED",
        "completed": False,
        "path": [{"x": 16, "y": 20, "t": 2}],
        "goal": [20, 20],
    })

    payload = bus.process_incoming()
    assert payload is not None
    assert bus.current_tick == 1
    assert payload["tick"] == 1

    # Verify ALL 3 robots in the payload are synchronized at tick 1!
    robots_dict = {r["robot_id"]: r for r in payload["robots"]}
    assert len(robots_dict) == 3
    assert robots_dict["G2P-01"]["x"] == 5 and robots_dict["G2P-01"]["y"] == 6
    assert robots_dict["G2P-02"]["x"] == 10 and robots_dict["G2P-02"]["y"] == 12
    assert robots_dict["G2P-03"]["x"] == 15 and robots_dict["G2P-03"]["y"] == 20
