"""
telemetry_bus.py — Decoupled Telemetry Aggregator and Storage.

Aggregates state updates pushed by independent robot processes and stores the latest
canonical TICK_UPDATE payload into logs/telemetry_state.json so that the FastAPI
viewer can connect, disconnect, be killed, and reconnect seamlessly.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[4]
LOG_DIR = ROOT_DIR / "logs"
TELEMETRY_FILE = LOG_DIR / "telemetry_state.json"


class TelemetryBus:
    """
    Collects telemetry events from the multiprocessing telemetry queue
    and persists the latest fleet state snapshot atomically.
    """

    def __init__(self, telemetry_queue: mp.Queue, fleet_size: int = 5) -> None:
        self.queue = telemetry_queue
        self.fleet_size = fleet_size
        self.current_tick: int = 0
        self.robot_states: Dict[str, Dict[str, Any]] = {}
        self.active_conflicts: List[Dict[str, Any]] = []
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def process_incoming(self) -> Optional[Dict[str, Any]]:
        """
        Drains available telemetry items from queue and updates snapshot.
        Returns the new TICK_UPDATE if tick advanced or updated, else None.
        """
        updated = False
        while not self.queue.empty():
            try:
                frame = self.queue.get_nowait()
            except Exception:
                break

            rid = frame["robot_id"]
            self.robot_states[rid] = frame
            tick = frame["tick"]
            if tick > self.current_tick:
                self.current_tick = tick
                self.active_conflicts.clear()

            if frame.get("conflict"):
                self.active_conflicts.append(frame["conflict"])

            updated = True

        if updated and self.robot_states:
            payload = self.build_tick_update()
            self.persist_state(payload)
            return payload
        return None

    def build_tick_update(self) -> Dict[str, Any]:
        """Builds SCHEMA.md §16 compliant TICK_UPDATE payload."""
        robots_list = []
        for rid, s in sorted(self.robot_states.items()):
            robots_list.append({
                "id": s["robot_id"],
                "robot_id": s["robot_id"],
                "position": {"x": s["x"], "y": s["y"]},
                "x": s["x"],
                "y": s["y"],
                "heading": s["heading"],
                "state": s["state"],
                "battery": s["battery_pct"],
                "battery_pct": s["battery_pct"],
                "current_task_id": s.get("current_task_id"),
                "priority_score": s["priority_score"],
                "wait_ticks_so_far": s["wait_ticks"],
                "action": s["action"],
                "completed": s["completed"],
                "path": s["path"],
                "goal": s["goal"],
                "conflict": s.get("conflict"),
                "planner_latency_ms": s.get("planner_latency_ms", 0.0),
            })

        return {
            "type": "TICK_UPDATE",
            "tick": self.current_tick,
            "timestamp_ms": int(time.time() * 1000),
            "robots": robots_list,
            "active_conflicts": list(self.active_conflicts),
            "temporary_obstacles": [],
        }

    def persist_state(self, payload: Dict[str, Any]) -> None:
        """Atomically writes payload to logs/telemetry_state.json."""
        tmp_file = LOG_DIR / f"telemetry_state_{os.getpid()}_{time.time_ns()}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            for _ in range(5):
                try:
                    os.replace(tmp_file, TELEMETRY_FILE)
                    return
                except OSError:
                    time.sleep(0.01)
            # Direct write fallback
            with open(TELEMETRY_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as e:
            pass
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass


def read_latest_telemetry() -> Optional[Dict[str, Any]]:
    """Reads latest telemetry snapshot written by the fleet processes."""
    if not TELEMETRY_FILE.exists():
        return None
    try:
        with open(TELEMETRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
