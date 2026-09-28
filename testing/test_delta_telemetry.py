"""
test_delta_telemetry.py

Unit and integration tests for FleetDeltaEncoder and ConnectionManager delta broadcasting.
Ensures delta encoding eliminates unchanged entities and guarantees full baseline delivery on reconnect.
"""

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
import pytest
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.websocket.delta_encoder import FleetDeltaEncoder
from app.websocket.connection_manager import ConnectionManager


class MockWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent_messages = []
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        if self.closed:
            raise RuntimeError("WebSocket closed")
        self.sent_messages.append(text)

    async def close(self, code: int = 1000):
        self.closed = True


def make_frame(tick: int, x: int = 10, y: int = 10, battery: float = 95.0, path_len: int = 5) -> Dict[str, Any]:
    return {
        "type": "TICK_UPDATE",
        "tick": tick,
        "timestamp_ms": 1000 + tick * 100,
        "robots": [
            {
                "robot_id": "AMR-01",
                "position": {"x": x, "y": y},
                "heading": "NORTH",
                "robot_type": "GOODS_TO_PERSON",
                "state": "EN_ROUTE_PICKUP",
                "battery_pct": battery,
                "current_task_id": "TASK-1",
                "priority_score": 0.75,
                "path": [{"x": x + i, "y": y, "t": i} for i in range(path_len)],
                "carrying_pod_id": None,
                "wait_ticks_so_far": 0,
                "action": "MOVE",
                "conflict": None,
            },
            {
                "robot_id": "AMR-02",
                "position": {"x": 20, "y": 20},
                "heading": "SOUTH",
                "robot_type": "SORTING",
                "state": "IDLE",
                "battery_pct": 88.0,
                "current_task_id": None,
                "priority_score": 0.40,
                "path": [],
                "carrying_pod_id": None,
                "wait_ticks_so_far": 0,
                "action": "IDLE",
                "conflict": None,
            },
        ],
        "tasks": [{"task_id": "TASK-1", "status": "IN_PROGRESS"}],
        "temporary_obstacles": [{"obstacle_id": "obs-1", "x": 5, "y": 5, "expires_at_tick": 100}],
        "inventory": [{"shelf_id": "S-1", "current_box_count": 10, "confidence": 0.99}],
        "fleet_status": {"running": True, "mode": "Autonomous", "tick": tick},
        "metrics": {"last_tick_processing_ms": 2.5},
    }


def test_delta_encoder_initial_tick():
    encoder = FleetDeltaEncoder()
    frame = make_frame(tick=0)
    delta = encoder.compute_delta(frame)

    assert delta["type"] == "TICK_DELTA"
    assert delta["tick"] == 0
    # On first frame, both robots are new and must be emitted
    assert len(delta["robots"]) == 2
    r_ids = {r["robot_id"] for r in delta["robots"]}
    assert r_ids == {"AMR-01", "AMR-02"}
    # Initial tasks, obstacles, inventory should be emitted
    assert "tasks" in delta
    assert "temporary_obstacles" in delta
    assert "inventory" in delta


def test_delta_encoder_unchanged_frame_omissions():
    encoder = FleetDeltaEncoder()
    frame0 = make_frame(tick=0)
    encoder.compute_delta(frame0)

    # Identical frame on next tick (only tick and timestamp changed)
    frame1 = make_frame(tick=1)
    delta1 = encoder.compute_delta(frame1)

    assert delta1["type"] == "TICK_DELTA"
    assert delta1["tick"] == 1
    # Neither robot moved or changed battery/state -> omit both
    assert len(delta1["robots"]) == 0
    # Static tasks, obstacles, inventory unchanged -> omitted from delta
    assert "tasks" not in delta1
    assert "temporary_obstacles" not in delta1
    assert "inventory" not in delta1


def test_delta_encoder_partial_robot_movement():
    encoder = FleetDeltaEncoder()
    frame0 = make_frame(tick=0, x=10, y=10)
    encoder.compute_delta(frame0)

    # In frame 1, AMR-01 moves to (11, 10), AMR-02 stays idle
    frame1 = make_frame(tick=1, x=11, y=10)
    delta1 = encoder.compute_delta(frame1)

    assert len(delta1["robots"]) == 1
    assert delta1["robots"][0]["robot_id"] == "AMR-01"
    assert delta1["robots"][0]["position"] == {"x": 11, "y": 10}


def test_delta_encoder_battery_noise_filtering():
    encoder = FleetDeltaEncoder()
    frame0 = make_frame(tick=0, battery=95.0)
    encoder.compute_delta(frame0)

    # Small jitter: 95.0 -> 95.05 (delta = 0.05 < 0.2)
    frame1 = make_frame(tick=1, battery=95.05)
    delta1 = encoder.compute_delta(frame1)
    assert len(delta1["robots"]) == 0

    # Meaningful change: 95.0 -> 94.7 (delta = 0.3 >= 0.2)
    frame2 = make_frame(tick=2, battery=94.7)
    delta2 = encoder.compute_delta(frame2)
    assert len(delta2["robots"]) == 1
    assert delta2["robots"][0]["battery_pct"] == 94.7


@pytest.mark.asyncio
async def test_connection_manager_broadcast_and_reconnect():
    mgr = ConnectionManager()
    ws_client1 = MockWebSocket()

    # 1. Client 1 connects before any telemetry
    await mgr.connect(ws_client1)
    assert ws_client1.accepted
    assert len(ws_client1.sent_messages) == 0  # No baseline cached yet

    # First telemetry tick: baseline + delta
    baseline_frame = make_frame(tick=0)
    full_json = json.dumps(baseline_frame)
    delta_json = json.dumps({"type": "TICK_DELTA", "tick": 0, "robots": []})

    await mgr.broadcast_telemetry(full_json, delta_json)

    # Client 1 was pending baseline, so it must have received full_json
    assert len(ws_client1.sent_messages) == 1
    received_0 = json.loads(ws_client1.sent_messages[0])
    assert received_0["type"] == "TICK_UPDATE"
    assert received_0["tick"] == 0

    # Second telemetry tick: Client 1 is established, receives compact delta
    delta_json_1 = json.dumps({"type": "TICK_DELTA", "tick": 1, "robots": [{"robot_id": "AMR-01", "x": 11, "y": 10}]})
    await mgr.broadcast_telemetry(full_json, delta_json_1)

    assert len(ws_client1.sent_messages) == 2
    received_1 = json.loads(ws_client1.sent_messages[1])
    assert received_1["type"] == "TICK_DELTA"
    assert received_1["tick"] == 1

    # 2. Reconnect Test: Client 2 connects while simulation is running at tick 1
    ws_client2 = MockWebSocket()
    await mgr.connect(ws_client2)

    # Client 2 MUST receive the latest full baseline immediately upon handshake
    assert len(ws_client2.sent_messages) == 1
    received_handshake = json.loads(ws_client2.sent_messages[0])
    assert received_handshake["type"] == "TICK_UPDATE"

    # Third telemetry tick: Both clients are established, both receive delta
    delta_json_2 = json.dumps({"type": "TICK_DELTA", "tick": 2, "robots": []})
    await mgr.broadcast_telemetry(full_json, delta_json_2)

    assert len(ws_client1.sent_messages) == 3
    assert len(ws_client2.sent_messages) == 2
    assert json.loads(ws_client1.sent_messages[-1])["type"] == "TICK_DELTA"
    assert json.loads(ws_client2.sent_messages[-1])["type"] == "TICK_DELTA"
