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
from app.core.config import get_settings

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
        max_ticks: int = 0,
        log_dir: Optional[Path] = None,
        map_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.map_data = map_data
        app_cfg = get_settings()

        if map_data is not None:
            from app.models.world import build_world_from_map_dict
            self.world, robot_starts = build_world_from_map_dict(map_data)
            self.obstacles = obstacles if obstacles is not None else sorted(list(self.world.static_obstacles))
            self.charging_stations = set(self.world.charging_stations)
            if robots_config is None:
                g2p_idx = 1
                sort_idx = 1
                audit_idx = 1
                self.robots_config = []
                for idx, r in enumerate(robot_starts, start=1):
                    rtype = r.get("type") or r.get("robot_type", "GOODS_TO_PERSON")
                    rid = r.get("id")
                    if not rid or rid.startswith("AMR-"):
                        if rtype == "SORTING":
                            rid = f"SORT-{sort_idx:02d}"
                            sort_idx += 1
                        elif rtype == "SCANNING_AUDIT":
                            rid = f"AUDIT-{audit_idx:02d}"
                            audit_idx += 1
                        else:
                            rid = f"G2P-{g2p_idx:02d}"
                            g2p_idx += 1
                    self.robots_config.append({
                        "robot_id": rid,
                        "start": (int(r["x"]), int(r["y"])),
                        "goal": None,
                        "urgency": 1,
                        "battery_pct": 100.0,
                        "robot_type": rtype,
                        "enable_idle_audit": app_cfg.AUTO_IDLE_AUDIT,
                        "auto_consolidation": app_cfg.AUTO_CONSOLIDATION,
                        "auto_transfer": app_cfg.AUTO_TRANSFER,
                    })
            else:
                self.robots_config = robots_config
        else:
            default_world = build_default_world()
            self.world = default_world
            self.obstacles = obstacles if obstacles is not None else sorted(list(default_world.static_obstacles))
            self.charging_stations = set(default_world.charging_stations)
            if robots_config is None:
                starts = [
                    (2, 4), (2, 9), (2, 15), (2, 24),
                    (27, 4), (27, 12), (27, 18), (27, 24),
                    (10, 3), (19, 3),
                ]
                robot_types = (["GOODS_TO_PERSON"] * 4 + ["SORTING"] * 3 + ["SCANNING_AUDIT"] * 3)
                robot_ids = [
                    "G2P-01", "G2P-02", "G2P-03", "G2P-04",
                    "SORT-01", "SORT-02", "SORT-03",
                    "AUDIT-01", "AUDIT-02", "AUDIT-03",
                ]
                self.robots_config = [
                    {
                        "robot_id": robot_ids[index - 1],
                        "start": start,
                        "goal": None,
                        "urgency": 1,
                        "battery_pct": 100.0,
                        "robot_type": robot_types[index - 1],
                        "enable_idle_audit": app_cfg.AUTO_IDLE_AUDIT,
                        "auto_consolidation": app_cfg.AUTO_CONSOLIDATION,
                        "auto_transfer": app_cfg.AUTO_TRANSFER,
                    }
                    for index, start in enumerate(starts, start=1)
                ]
            else:
                self.robots_config = robots_config

        self.tick_interval_s = tick_interval_s
        self.max_ticks = max_ticks
        self.log_dir = log_dir or (ROOT_DIR / "logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Cloud / Render memory-guard: Use threads to stay safely under 512MB RAM when running in cloud/containers
        is_cloud = bool(
            os.environ.get("RENDER")
            or os.environ.get("RAILWAY_STATIC_URL")
            or os.environ.get("FLY_APP_NAME")
            or os.environ.get("DOCKER_CONTAINER")
            or os.environ.get("CONTAINER")
        )
        self.use_threads = os.environ.get("USE_THREADED_WORKERS", "1" if is_cloud else "0") == "1"

        if self.use_threads:
            import queue
            self.telemetry_queue = queue.Queue()
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.start_event = threading.Event()
            class SpeedHolder:
                def __init__(self, val: float = 1.0): self.value = float(val)
            self.speed_multiplier = SpeedHolder(1.0)
            self.ready_barrier = None
        else:
            self.telemetry_queue: mp.Queue = mp.Queue()
            self.stop_event: mp.Event = mp.Event()
            self.pause_event: mp.Event = mp.Event()
            self.start_event: mp.Event = mp.Event()
            self.speed_multiplier: mp.Value = mp.Value('d', 1.0)
            self.ready_barrier: Optional[mp.Barrier] = None

        # Distinct UDP ports for real decentralized networking (e.g. 9000 + N for robots, 9601..9603 for stations)
        self.peer_ports: Dict[str, int] = {
            cfg["robot_id"]: 9000 + i for i, cfg in enumerate(self.robots_config, start=1)
        }
        self.peer_ports.update(DEFAULT_STATION_PORTS)
        self.processes: List[Any] = []
        self.station_processes: List[Any] = []
        self.bus = TelemetryBus(self.telemetry_queue, fleet_size=len(self.robots_config))
        self._bus_thread: Optional[threading.Thread] = None

    def start(self, enable_stations: bool = True) -> None:
        """Starts all independent robot processes, fixed station processes, and the telemetry aggregator."""
        print(f"[FleetOrchestrator] Spawning {len(self.robots_config)} independent robot processes...")

        # 1. Start Telemetry Bus collector thread
        self._bus_thread = threading.Thread(target=self._run_bus, daemon=True, name="TelemetryBusCollector")
        self._bus_thread.start()

        # 2. Determine fixed station specs first so barrier participant count is exact
        station_specs = []
        if enable_stations:
            if hasattr(self, "world") and self.world and getattr(self.world, "fixed_stations", None):
                for st in self.world.fixed_stations.values():
                    station_specs.append((
                        st["id"],
                        st["role"],
                        (st["x"], st["y"]),
                        st.get("port", DEFAULT_STATION_PORTS.get(st["role"], 9601)),
                    ))
            else:
                station_specs = [
                    ("IMPORT_STATION", "IMPORT_STATION", (1, 14), DEFAULT_STATION_PORTS["IMPORT_STATION"]),
                    ("EXPORT_STATION", "EXPORT_STATION", (28, 14), DEFAULT_STATION_PORTS["EXPORT_STATION"]),
                    ("AUTHORITY_STATION", "AUTHORITY_STATION", (15, 14), DEFAULT_STATION_PORTS["AUTHORITY_STATION"]),
                ]

        total_nodes = len(self.robots_config) + len(station_specs)
        if self.use_threads:
            self.ready_barrier = threading.Barrier(total_nodes + 1)
            self.start_event = threading.Event()
        else:
            self.ready_barrier = mp.Barrier(total_nodes + 1)
            self.start_event = mp.Event()

        # 3. Spawn workers per robot (threads for low-memory cloud, processes for dedicated bare-metal)
        fleet_roster = {cfg["robot_id"]: cfg.get("robot_type", "GOODS_TO_PERSON") for cfg in self.robots_config}
        app_cfg = get_settings()
        worker_cls = threading.Thread if self.use_threads else mp.Process

        for cfg in self.robots_config:
            rid = cfg["robot_id"]
            enable_audit = cfg.get("enable_idle_audit", app_cfg.AUTO_IDLE_AUDIT)
            p = worker_cls(
                target=run_robot_process,
                name=f"Worker-{rid}",
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
                    enable_audit,
                    self.pause_event,
                    fleet_roster,
                ),
                kwargs={
                    "start_event": self.start_event,
                    "start_barrier": self.ready_barrier,
                    "auto_idle_audit": enable_audit,
                    "auto_consolidation": cfg.get("auto_consolidation", app_cfg.AUTO_CONSOLIDATION),
                    "auto_transfer": cfg.get("auto_transfer", app_cfg.AUTO_TRANSFER),
                    "map_data": self.map_data,
                    "speed_multiplier": self.speed_multiplier,
                },
            )

            p.start()
            self.processes.append(p)
            pid_str = f"PID={getattr(p, 'pid', os.getpid())}" if not self.use_threads else f"ThreadID={p.ident}"
            print(f"  -> Spawned Worker for {rid} ({pid_str})")

        # 4. Spawn fixed-infrastructure Station workers if enabled
        if station_specs:
            print("[FleetOrchestrator] Spawning fixed station workers (Import, Export, Authority)...")
            for st_id, st_role, st_pos, st_port in station_specs:
                sp = worker_cls(
                    target=run_station_process,
                    name=f"Worker-{st_id}",
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
                    kwargs={
                        "start_event": self.start_event,
                        "start_barrier": self.ready_barrier,
                        "pause_event": self.pause_event,
                        "speed_multiplier": self.speed_multiplier,
                    },
                )
                sp.start()
                self.station_processes.append(sp)
                pid_str = f"PID={getattr(sp, 'pid', os.getpid())}" if not self.use_threads else f"ThreadID={sp.ident}"
                print(f"  -> Spawned Station Worker for {st_id} ({pid_str}) on UDP port {st_port}")

        # Synchronize child processes: Wait until every process has finished initialization
        try:
            self.ready_barrier.wait(timeout=3.0)
        except Exception as e:
            print(f"[FleetOrchestrator] Barrier rendezvous complete/skipped: {e}")
        self.start_event.set()
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
            from app.services.telemetry_bus import reset_telemetry_cache
            reset_telemetry_cache()
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

        current_speed = self.get_speed()
        # Re-initialize events and queue
        if self.use_threads:
            import queue
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.start_event = threading.Event()
            class SpeedHolder:
                def __init__(self, val: float = 1.0): self.value = float(val)
            self.speed_multiplier = SpeedHolder(current_speed)
            if pause_on_reset:
                self.pause_event.set()
            self.telemetry_queue = queue.Queue()
            self.processes = []
            self.station_processes = []
            self.bus = TelemetryBus(self.telemetry_queue, fleet_size=len(self.robots_config))
        else:
            self.stop_event = mp.Event()
            self.pause_event = mp.Event()
            self.start_event = mp.Event()
            self.speed_multiplier = mp.Value('d', current_speed)
            if pause_on_reset:
                self.pause_event.set()
            self.telemetry_queue = mp.Queue()
            self.processes = []
            self.station_processes = []
            self.bus = TelemetryBus(self.telemetry_queue, fleet_size=len(self.robots_config))

        self.start()
        print("[FleetOrchestrator] Robot processes restarted in initial state.")

    def set_speed(self, speed: float) -> None:
        """Sets the simulation speed multiplier across all processes."""
        if hasattr(self, "speed_multiplier"):
            self.speed_multiplier.value = float(max(0.1, min(speed, 10.0)))

    def get_speed(self) -> float:
        """Gets the current simulation speed multiplier."""
        if hasattr(self, "speed_multiplier"):
            return float(self.speed_multiplier.value)
        return 1.0

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
        self.start_event.set()
        if self.ready_barrier is not None:
            try:
                self.ready_barrier.abort()
            except Exception:
                pass
        if self._bus_thread and self._bus_thread.is_alive():
            self._bus_thread.join(timeout=1.0)

        for p in self.processes:
            p.join(timeout=0.2)
            if not self.use_threads and p.is_alive() and getattr(p, "pid", None):
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
            if not self.use_threads and sp.is_alive() and getattr(sp, "pid", None):
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
