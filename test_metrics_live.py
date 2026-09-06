"""
test_metrics_live.py — Live Metrics Aggregation Verification.

Asserts that processing TICK_UPDATE frames through the forwarder logic
populates Telemetry with live values (active_conflicts, replans, planner_latency_ms,
last_tick_processing_ms) instead of leaving them as inert zeroes.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.main import process_telemetry_frame
from app.services.fleet_state import FleetState
from app.services.telemetry import Telemetry


def test_metrics_live():
    fleet_state = FleetState()
    telemetry = Telemetry()

    initial_snap = telemetry.snapshot()
    assert initial_snap["active_conflicts"] == 0
    assert initial_snap["replans"] == 0
    assert initial_snap["last_tick_processing_ms"] == 0.0
    assert initial_snap["planner_latency_ms"] == 0.0

    # Inject frame 1 with active conflict and robot replan/conflict
    frame1 = {
        "type": "TICK_UPDATE",
        "tick": 1,
        "timestamp_ms": 1000,
        "active_conflicts": [
            {"cell": {"x": 5, "y": 6}, "robot_ids": ["AMR-01", "AMR-02"]}
        ],
        "robots": [
            {
                "id": "AMR-01",
                "position": {"x": 5, "y": 6},
                "heading": "NORTH",
                "battery": 92.5,
                "priority_score": 1400.0,
                "wait_ticks_so_far": 2,
                "conflict": {"winner_id": "AMR-02", "loser_id": "AMR-01"},
                "planner_latency_ms": 1.85,
            },
            {
                "id": "AMR-02",
                "position": {"x": 5, "y": 7},
                "heading": "SOUTH",
                "battery": 88.0,
                "priority_score": 1600.0,
                "wait_ticks_so_far": 0,
                "conflict": None,
                "planner_latency_ms": 1.20,
            },
        ],
    }

    process_telemetry_frame(frame1, fleet_state, telemetry, loop_duration_ms=3.45)

    snap1 = telemetry.snapshot()
    assert snap1["active_conflicts"] == 1, f"Expected 1 active conflict, got {snap1['active_conflicts']}"
    assert snap1["replans"] == 1, f"Expected 1 replan, got {snap1['replans']}"
    assert snap1["active_robots"] == 2, f"Expected 2 active robots, got {snap1['active_robots']}"
    assert snap1["last_tick_processing_ms"] > 0, "last_tick_processing_ms was zero"
    assert snap1["planner_latency_ms"] > 0, "planner_latency_ms was zero"

    # Inject frame 2 with another conflict
    frame2 = {
        "type": "TICK_UPDATE",
        "tick": 2,
        "timestamp_ms": 1500,
        "active_conflicts": [
            {"cell": {"x": 10, "y": 12}, "robot_ids": ["AMR-03", "AMR-04"]}
        ],
        "robots": [
            {
                "id": "AMR-03",
                "position": {"x": 10, "y": 12},
                "heading": "EAST",
                "battery": 90.0,
                "conflict": {"winner_id": "AMR-04", "loser_id": "AMR-03"},
                "planner_latency_ms": 2.10,
            }
        ],
    }

    process_telemetry_frame(frame2, fleet_state, telemetry, loop_duration_ms=4.10)

    snap2 = telemetry.snapshot()
    assert snap2["active_conflicts"] == 1
    assert snap2["replans"] == 2, f"Expected 2 cumulative replans, got {snap2['replans']}"
    assert snap2["planner_latency_ms"] > 0

    print("PHASE 5 VERIFICATION PASSED: Telemetry metrics successfully reflect live decentralized data!")


if __name__ == "__main__":
    test_metrics_live()
