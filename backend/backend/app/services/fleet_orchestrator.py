"""
fleet_orchestrator.py — Multi-Process Decentralized Fleet Orchestrator.

Spawns each robot in its own independent OS process, provides peer-to-peer communication
mailboxes, and launches the decoupled telemetry aggregator.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "testing"))

from app.services.robot_node import run_robot_process
from app.services.telemetry_bus import TelemetryBus
from app.models.world import build_default_world
from full_integration_test import get_static_shelves

log = logging.getLogger(__name__)


class FleetOrchestrator:
    """
    Manages the lifecycle of the decentralized multi-process AMR fleet.
    """

    def __init__(
        self,
        robots_config: Optional[List[Dict[str, Any]]] = None,
        obstacles: Optional[List[Tuple[int, int]]] = None,
        tick_interval_s: float = 0.15,
        max_ticks: int = 150,
        log_dir: Optional[Path] = None,
    ) -> None:
        self.obstacles = obstacles if obstacles is not None else get_static_shelves()
        self.charging_stations = set(build_default_world().charging_stations)
        self.tick_interval_s = tick_interval_s
        self.max_ticks = max_ticks
        self.log_dir = log_dir or (ROOT_DIR / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        if robots_config is None:
            # Match the backend fleet size. Robots remain idle until a REST job
            # is dispatched, so demo trajectories cannot steal job capacity.
            starts = [(1, 28), (3, 28), (5, 28), (7, 28), (9, 28),
                      (11, 28), (13, 28), (15, 28), (17, 28), (19, 28)]
            robot_types = (["GOODS_TO_PERSON"] * 4
                           + ["SORTING"] * 3
                           + ["SCANNING_AUDIT"] * 3)
            self.robots_config = [
                {
                    "robot_id": f"AMR-{index:02d}",
                    "start": start,
                    "goal": start,
                    "urgency": 1,
                    "battery_pct": 100.0,
                    "robot_type": robot_types[index - 1],
                }
                for index, start in enumerate(starts, start=1)
            ]
        else:
            self.robots_config = robots_config

        self.telemetry_queue: mp.Queue = mp.Queue()
        self.stop_event: mp.Event = mp.Event()
        # Distinct UDP ports for real decentralized networking (e.g. 9000 + N)
        self.peer_ports: Dict[str, int] = {
            cfg["robot_id"]: 9000 + i for i, cfg in enumerate(self.robots_config, start=1)
        }
        self.processes: List[mp.Process] = []
        self.bus = TelemetryBus(self.telemetry_queue, fleet_size=len(self.robots_config))
        self._bus_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Starts all independent robot processes and the telemetry aggregator."""
        print(f"[FleetOrchestrator] Spawning {len(self.robots_config)} independent robot processes...")

        # 1. Start Telemetry Bus collector thread
        self._bus_thread = threading.Thread(target=self._run_bus, daemon=True, name="TelemetryBusCollector")
        self._bus_thread.start()

        # 2. Spawn one OS process per robot
        for cfg in self.robots_config:
            rid = cfg["robot_id"]
            p = mp.Process(
                target=run_robot_process,
                name=f"Process-{rid}",
                args=(
                    rid,
                    cfg["start"],
                    cfg["goal"],
                    cfg["urgency"],
                    cfg["battery_pct"],
                    self.obstacles,
                    self.peer_ports[rid],
                    self.peer_ports,
                    self.telemetry_queue,
                    self.stop_event,
                    str(self.log_dir),
                    self.tick_interval_s,
                    self.max_ticks,
                    self.charging_stations,
                    cfg.get("robot_type", "GOODS_TO_PERSON"),
                ),
            )
            p.start()
            self.processes.append(p)
            print(f"  -> Spawned Process for {rid} (PID={p.pid})")

        print("[FleetOrchestrator] All robot processes successfully running!")

    def _run_bus(self) -> None:
        """Background thread collecting telemetry frames from robot processes."""
        while not self.stop_event.is_set():
            self.bus.process_incoming()
            time.sleep(0.02)

    def set_packet_loss(self, pct: float) -> None:
        """Sets packet loss percentage across fleet."""
        self.packet_loss_pct = pct

    def is_alive(self) -> bool:
        """Returns True if any robot process is currently active."""
        return any(p.is_alive() for p in self.processes)

    def stop(self) -> None:
        """Signals all processes to stop and joins them."""
        print("[FleetOrchestrator] Stopping all robot processes...")
        self.stop_event.set()
        for p in self.processes:
            p.join(timeout=1.0)
            if p.is_alive():
                p.terminate()
        print("[FleetOrchestrator] All robot processes stopped.")


if __name__ == "__main__":
    mp.freeze_support()
    orchestrator = FleetOrchestrator()
    try:
        orchestrator.start()
        print("[Main] Fleet running. Press Ctrl+C to stop.")
        while orchestrator.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        orchestrator.stop()
