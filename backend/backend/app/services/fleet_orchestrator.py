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
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "archive" / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "testing"))

from app.services.robot_node import run_robot_process
from app.services.station_node import DEFAULT_STATION_PORTS, run_station_process
from app.services.telemetry_bus import TelemetryBus
from app.models.world import build_default_world

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
        default_world = build_default_world()
        self.obstacles = obstacles if obstacles is not None else sorted(list(default_world.static_obstacles))
        self.charging_stations = set(default_world.charging_stations)
        self.tick_interval_s = tick_interval_s
        self.max_ticks = max_ticks
        self.log_dir = log_dir or (ROOT_DIR / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        if robots_config is None:
            # Distributed starting positions across West inbound docks, East outbound docks,
            # and North/South transit highway staging lanes with immediate highway egress.
            starts = [
                (2, 4),   # AMR-01: West highway staging bay 1
                (2, 9),   # AMR-02: West highway staging bay 2
                (2, 15),  # AMR-03: West highway staging bay 3
                (2, 24),  # AMR-04: West highway staging bay 4
                (27, 4),  # AMR-05: East highway staging bay 1
                (27, 12), # AMR-06: East highway staging bay 2
                (27, 18), # AMR-07: East highway staging bay 3
                (27, 24), # AMR-08: East highway staging bay 4
                (10, 3),  # AMR-09: North central cross-highway (x=10)
                (19, 3),  # AMR-10: North central cross-highway (x=19)
            ]
            robot_types = (["GOODS_TO_PERSON"] * 4
                           + ["SORTING"] * 3
                           + ["SCANNING_AUDIT"] * 3)
            self.robots_config = [
                {
                    "robot_id": f"AMR-{index:02d}",
                    "start": start,
                    "goal": None,
                    "urgency": 1,
                    "battery_pct": 100.0,
                    "robot_type": robot_types[index - 1],
                    "enable_idle_audit": (robot_types[index - 1] == "SCANNING_AUDIT"),
                }
                for index, start in enumerate(starts, start=1)
            ]
        else:
            self.robots_config = robots_config

        self.telemetry_queue: mp.Queue = mp.Queue()
        self.stop_event: mp.Event = mp.Event()
        self.pause_event: mp.Event = mp.Event()
        # Distinct UDP ports for real decentralized networking (e.g. 9000 + N for robots, 9601..9603 for stations)
        self.peer_ports: Dict[str, int] = {
            cfg["robot_id"]: 9000 + i for i, cfg in enumerate(self.robots_config, start=1)
        }
        self.peer_ports.update(DEFAULT_STATION_PORTS)
        self.processes: List[mp.Process] = []
        self.station_processes: List[mp.Process] = []
        self.bus = TelemetryBus(self.telemetry_queue, fleet_size=len(self.robots_config))
        self._bus_thread: Optional[threading.Thread] = None

    def start(self, enable_stations: bool = True) -> None:
        """Starts all independent robot processes, fixed station processes, and the telemetry aggregator."""
        print(f"[FleetOrchestrator] Spawning {len(self.robots_config)} independent robot processes...")

        # 1. Start Telemetry Bus collector thread
        self._bus_thread = threading.Thread(target=self._run_bus, daemon=True, name="TelemetryBusCollector")
        self._bus_thread.start()

        # 2. Spawn one OS process per robot
        fleet_roster = {cfg["robot_id"]: cfg.get("robot_type", "GOODS_TO_PERSON") for cfg in self.robots_config}
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
                    cfg.get("enable_idle_audit", True),
                    self.pause_event,
                    fleet_roster,
                ),
            )

            p.start()
            self.processes.append(p)
            print(f"  -> Spawned Process for {rid} (PID={p.pid})")

        # 3. Spawn 3 fixed-infrastructure Station processes if enabled
        if enable_stations:
            print("[FleetOrchestrator] Spawning 3 fixed station processes (Import, Export, Authority)...")
            station_specs = [
                ("IMPORT_STATION", "IMPORT_STATION", (1, 14), DEFAULT_STATION_PORTS["IMPORT_STATION"]),
                ("EXPORT_STATION", "EXPORT_STATION", (28, 14), DEFAULT_STATION_PORTS["EXPORT_STATION"]),
                ("AUTHORITY_STATION", "AUTHORITY_STATION", (15, 14), DEFAULT_STATION_PORTS["AUTHORITY_STATION"]),
            ]
            for st_id, st_role, st_pos, st_port in station_specs:
                sp = mp.Process(
                    target=run_station_process,
                    name=f"Process-{st_id}",
                    args=(
                        st_id,
                        st_role,
                        st_pos,
                        st_port,
                        self.peer_ports,
                        self.stop_event,
                        str(self.log_dir),
                        self.tick_interval_s,
                    ),
                )
                sp.start()
                self.station_processes.append(sp)
                print(f"  -> Spawned Station Process for {st_id} (PID={sp.pid}) on UDP port {st_port}")

        print("[FleetOrchestrator] All robot and station processes successfully running!")

    def pause(self) -> None:
        """Pauses ticking and logging across all robot processes."""
        self.pause_event.set()

    def resume(self) -> None:
        """Resumes ticking and logging across all robot processes."""
        self.pause_event.clear()

    def is_paused(self) -> bool:
        """Returns True if the fleet processes are paused."""
        return self.pause_event.is_set()

    def initial_robots_telemetry(self) -> List[Dict[str, Any]]:
        """Returns the initial snapshot of all robots at their starting bays."""
        return [
            {
                "robot_id": cfg["robot_id"],
                "id": cfg["robot_id"],
                "position": {"x": cfg["start"][0], "y": cfg["start"][1]},
                "heading": "NORTH",
                "state": "IDLE",
                "robot_type": cfg.get("robot_type", "GOODS_TO_PERSON"),
                "battery": 100.0,
                "battery_pct": 100.0,
                "current_task_id": None,
                "priority_score": 0.0,
                "path": [],
                "conflict": None,
                "wait_ticks_so_far": 0,
            }
            for cfg in self.robots_config
        ]

    def reset_logs(self) -> None:
        """Truncates all robot log files and resets telemetry_state.json with initial robot states."""
        for log_file in self.log_dir.glob("robot_*.log"):
            try:
                with open(log_file, "w", encoding="utf-8") as f:
                    pass
            except Exception:
                pass
        t_file = self.log_dir / "telemetry_state.json"
        try:
            import json
            with open(t_file, "w", encoding="utf-8") as f:
                json.dump({
                    "type": "TICK_UPDATE",
                    "tick": 0,
                    "timestamp_ms": int(time.time() * 1000),
                    "robots": self.initial_robots_telemetry(),
                    "active_conflicts": [],
                    "temporary_obstacles": [],
                }, f)
        except Exception:
            pass

    def reset(self, pause_on_reset: bool = True) -> None:
        """Fully resets all robot processes back to initial starting bays and battery."""
        print("[FleetOrchestrator] Stopping processes for reset...")
        self.stop()
        time.sleep(0.25)
        self.reset_logs()

        # Re-initialize events and queue
        self.stop_event = mp.Event()
        self.pause_event = mp.Event()
        if pause_on_reset:
            self.pause_event.set()
        self.telemetry_queue = mp.Queue()
        self.processes = []
        self.bus = TelemetryBus(self.telemetry_queue, fleet_size=len(self.robots_config))

        self.start()
        print("[FleetOrchestrator] Robot processes restarted in initial state.")

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
        """Signals all processes to stop and forcefully joins/terminates them."""
        print("[FleetOrchestrator] Stopping all robot and station processes...")
        self.stop_event.set()
        if self._bus_thread and self._bus_thread.is_alive():
            self._bus_thread.join(timeout=1.0)
        for p in self.processes:
            p.join(timeout=0.2)
            if p.is_alive() and p.pid:
                try:
                    if sys.platform == "win32":
                        import subprocess
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        p.terminate()
                except Exception:
                    pass
                p.join(timeout=0.2)
        self.processes.clear()

        for sp in self.station_processes:
            sp.join(timeout=0.2)
            if sp.is_alive() and sp.pid:
                try:
                    if sys.platform == "win32":
                        import subprocess
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(sp.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        sp.terminate()
                except Exception:
                    pass
                sp.join(timeout=0.2)
        self.station_processes.clear()

        # Clean any zombie processes holding AMR UDP ports
        if sys.platform == "win32":
            import subprocess
            try:
                out = subprocess.check_output(["netstat", "-ano", "-p", "udp"], text=True)
                my_pid = os.getpid()
                ports = list(self.peer_ports.values())
                for line in out.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 4 and parts[0].upper() == "UDP":
                        addr = parts[1]
                        pid_str = parts[-1]
                        for port in ports:
                            if f":{port}" in addr:
                                try:
                                    pid = int(pid_str)
                                    if pid != my_pid and pid > 0:
                                        subprocess.run(
                                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                        )
                                except Exception:
                                    pass
            except Exception:
                pass

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
