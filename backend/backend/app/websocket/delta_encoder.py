"""
delta_encoder.py — Compact Delta Encoding for Live Fleet Telemetry.

Computes compact delta frames between consecutive simulation ticks:
1. Baseline Frame: Sent on client handshake/reconnect (full world state, all robots, paths, inventory, obstacles).
2. Delta Frame: Sent on subsequent ticks (only changed robot positions, state, battery, heading;
   omits unchanged static obstacles, unchanged paths, and static inventory).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple


class FleetDeltaEncoder:
    """
    Tracks prior fleet telemetry state and computes compact diffs.
    """

    def __init__(self) -> None:
        self._prev_robots: Dict[str, Dict[str, Any]] = {}
        self._prev_tasks_sig: Optional[str] = None
        self._prev_obstacles_sig: Optional[str] = None
        self._prev_inventory_sig: Optional[str] = None
        self._prev_conflicts_sig: Optional[str] = None
        self._prev_chutes_sig: Optional[str] = None

    def reset(self) -> None:
        """Resets encoder state."""
        self._prev_robots.clear()
        self._prev_tasks_sig = None
        self._prev_obstacles_sig = None
        self._prev_inventory_sig = None
        self._prev_conflicts_sig = None
        self._prev_chutes_sig = None

    def compute_delta(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        """
        Computes a compact TICK_DELTA dictionary from the full tick frame.
        Only robots whose position, heading, state, battery, action, or path changed are included.
        Unchanged static or repeated objects are omitted.
        """
        current_robots = frame.get("robots", [])
        changed_robots: List[Dict[str, Any]] = []

        for r in current_robots:
            rid = r.get("id") or r.get("robot_id")
            if not rid:
                continue

            prev = self._prev_robots.get(rid)
            pos_tuple = (
                r.get("x", r.get("position", {}).get("x")),
                r.get("y", r.get("position", {}).get("y")),
            )
            heading = r.get("heading")
            state = r.get("state")
            action = r.get("action")
            battery = round(float(r.get("battery_pct", r.get("battery", 100.0))), 1)
            priority = round(float(r.get("priority_score", 0.0)), 2)
            pod = r.get("carrying_pod_id")
            path = r.get("path", [])
            path_len = len(path)
            path_sig = (path_len, path[0]["x"], path[0]["y"], path[-1]["x"], path[-1]["y"]) if path_len > 0 else (0, 0, 0, 0, 0)

            needs_send = False
            path_changed = False

            if prev is None:
                needs_send = True
                path_changed = True
            else:
                if prev["pos"] != pos_tuple:
                    needs_send = True
                if prev["heading"] != heading:
                    needs_send = True
                if prev["state"] != state:
                    needs_send = True
                if prev["action"] != action:
                    needs_send = True
                if abs(prev["battery"] - battery) >= 0.2:
                    needs_send = True
                if prev["pod"] != pod:
                    needs_send = True
                if prev["path_sig"] != path_sig:
                    needs_send = True
                    path_changed = True

            # Update cache
            self._prev_robots[rid] = {
                "pos": pos_tuple,
                "heading": heading,
                "state": state,
                "action": action,
                "battery": battery,
                "pod": pod,
                "path_sig": path_sig,
            }

            if needs_send:
                robot_delta: Dict[str, Any] = {
                    "id": rid,
                    "robot_id": rid,
                    "x": pos_tuple[0],
                    "y": pos_tuple[1],
                    "position": {"x": pos_tuple[0], "y": pos_tuple[1]},
                    "heading": heading,
                    "state": state,
                    "battery_pct": battery,
                    "battery": battery,
                    "priority_score": priority,
                    "action": action,
                    "carrying_pod_id": pod,
                    "current_task_id": r.get("current_task_id"),
                }
                if r.get("conflict"):
                    robot_delta["conflict"] = r["conflict"]
                if path_changed:
                    robot_delta["path"] = path

                changed_robots.append(robot_delta)

        # Build delta frame
        delta: Dict[str, Any] = {
            "type": "TICK_DELTA",
            "tick": frame.get("tick", 0),
            "timestamp_ms": frame.get("timestamp_ms", 0),
            "robots": changed_robots,
        }

        # Check tasks changes
        tasks = frame.get("tasks")
        if tasks is not None:
            tasks_sig = f"{len(tasks)}:" + ",".join(f"{t['task_id']}={t.get('status')}" for t in tasks)
            if tasks_sig != self._prev_tasks_sig:
                delta["tasks"] = tasks
                self._prev_tasks_sig = tasks_sig

        # Check temporary obstacles changes
        obstacles = frame.get("temporary_obstacles")
        if obstacles is not None:
            obs_sig = f"{len(obstacles)}:" + ",".join(f"{o.get('obstacle_id')}_{o.get('expires_at_tick')}" for o in obstacles)
            if obs_sig != self._prev_obstacles_sig:
                delta["temporary_obstacles"] = obstacles
                self._prev_obstacles_sig = obs_sig

        # Active conflicts
        conflicts = frame.get("active_conflicts")
        if conflicts:
            delta["active_conflicts"] = conflicts
            self._prev_conflicts_sig = str(conflicts)
        elif self._prev_conflicts_sig:
            delta["active_conflicts"] = []
            self._prev_conflicts_sig = None

        # Check inventory changes
        inventory = frame.get("inventory")
        if inventory is not None:
            inv_sig = f"{len(inventory)}:" + ",".join(f"{s.get('shelf_id')}={s.get('current_box_count')}_{s.get('confidence')}" for s in inventory)
            if inv_sig != self._prev_inventory_sig:
                delta["inventory"] = inventory
                self._prev_inventory_sig = inv_sig

        # Fleet status & metrics
        if "fleet_status" in frame:
            delta["fleet_status"] = frame["fleet_status"]
        if "metrics" in frame:
            delta["metrics"] = frame["metrics"]
        if "halow_status" in frame:
            delta["halow_status"] = frame["halow_status"]

        return delta
