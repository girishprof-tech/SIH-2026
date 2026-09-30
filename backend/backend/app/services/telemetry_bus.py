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


_IN_MEMORY_LATEST_TELEMETRY: Optional[Dict[str, Any]] = None


class TelemetryBus:
    """
    Collects telemetry events from the telemetry queue
    and persists the latest fleet state snapshot atomically.
    """

    def __init__(self, telemetry_queue: mp.Queue, fleet_size: int = 5) -> None:
        self.queue = telemetry_queue
        self.fleet_size = fleet_size
        self.current_tick: int = 0
        self.robot_states: Dict[str, Dict[str, Any]] = {}
        self.frames_by_tick: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self.active_conflicts: List[Dict[str, Any]] = []
        self._last_disk_write: float = 0.0
        self._last_tick_advance: float = time.time()
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def process_incoming(self) -> Optional[Dict[str, Any]]:
        """
        Drains available telemetry items from queue and updates snapshot.
        Synchronizes robot ticks so all robots in the fleet advance in lockstep
        without dropping intermediate movement waypoints or skipping tiles.
        """
        updated = False
        while not self.queue.empty():
            try:
                frame = self.queue.get_nowait()
            except Exception:
                break

            rid = frame["robot_id"]
            tick = frame["tick"]
            self.robot_states[rid] = frame

            if tick not in self.frames_by_tick:
                self.frames_by_tick[tick] = {}
            self.frames_by_tick[tick][rid] = frame

            if frame.get("conflict"):
                self.active_conflicts.append(frame["conflict"])

            updated = True

        if not updated and not self.frames_by_tick:
            return None

        now = time.time()
        advanced = False

        # Attempt to advance through completed ticks sequentially
        while True:
            next_tick = self.current_tick + 1
            tick_frames = self.frames_by_tick.get(next_tick, {})
            # Quorum: all robots in fleet reported for this tick
            has_quorum = len(tick_frames) >= self.fleet_size
            # Fallback timeout: if one robot stalled or future ticks have already arrived
            future_ticks_exist = any(t > next_tick for t in self.frames_by_tick.keys())
            is_timed_out = (now - self._last_tick_advance) > 0.45 or future_ticks_exist

            if has_quorum or (is_timed_out and len(tick_frames) > 0):
                # Apply all frames from next_tick to authoritative snapshot
                for rid, f in tick_frames.items():
                    self.robot_states[rid] = f
                self.current_tick = next_tick
                self._last_tick_advance = now
                self.active_conflicts.clear()
                # Clean up expired ticks
                stale_ticks = [t for t in self.frames_by_tick if t <= next_tick]
                for st in stale_ticks:
                    self.frames_by_tick.pop(st, None)
                advanced = True
            else:
                break

        # Initial baseline snapshot at tick 0 before simulation advances
        global _IN_MEMORY_LATEST_TELEMETRY
        if not advanced and self.current_tick == 0 and self.robot_states and _IN_MEMORY_LATEST_TELEMETRY is None:
            advanced = True

        if advanced and self.robot_states:
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
                "tick": s.get("tick", self.current_tick),
                "robot_type": s.get("robot_type", "GOODS_TO_PERSON"),
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
                "carrying_pod_id": s.get("carrying_pod_id"),
            })

        from ..core.config import cfg
        return {
            "type": "TICK_UPDATE",
            "tick": self.current_tick,
            "tick_ms": cfg.SIM_TICK_MS,
            "timestamp_ms": int(time.time() * 1000),
            "robots": robots_list,
            "active_conflicts": list(self.active_conflicts),
            "temporary_obstacles": [],
        }

    def persist_state(self, payload: Dict[str, Any]) -> None:
        """Stores payload in memory instantly, and writes to disk at throttled rate."""
        global _IN_MEMORY_LATEST_TELEMETRY
        _IN_MEMORY_LATEST_TELEMETRY = payload

        now = time.time()
        # Throttle disk writes to at most 4 times a second to eliminate container disk queue bottlenecks
        if (now - self._last_disk_write) < 0.25:
            return
        self._last_disk_write = now

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
        except Exception:
            pass
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass


def read_latest_telemetry() -> Optional[Dict[str, Any]]:
    """Reads latest telemetry snapshot, prioritizing in-memory cache then disk."""
    global _IN_MEMORY_LATEST_TELEMETRY
    if _IN_MEMORY_LATEST_TELEMETRY is not None:
        return _IN_MEMORY_LATEST_TELEMETRY
    if not TELEMETRY_FILE.exists():
        return None
    try:
        with open(TELEMETRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def reset_telemetry_cache() -> None:
    """Resets in-memory telemetry snapshot cache on fleet restart or reset."""
    global _IN_MEMORY_LATEST_TELEMETRY
    _IN_MEMORY_LATEST_TELEMETRY = None

