"""
SIH2026 — Edge-AI Distributed Fleet Coordination Backend
Member 4: Backend & Edge Simulation Broker

Entry point: uvicorn app.main:app --reload

Architecture:
  - FleetState:          authoritative in-memory simulation state
  - SimulationEngine:    simulation tick loop (asyncio)
  - ReservationManager:  space-time reservation table
  - TaskManager:         task lifecycle and assignment
  - ConflictManager:     conflict detection and resolution
  - ConnectionManager:   WebSocket broadcast
  - Telemetry:           performance metrics

All hot-path state is in memory. No database in the simulation loop.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.api import chaos, robots, simulation, tasks, websocket
from app.api.chaos_and_world import router as world_router
from app.api.map_router import router as map_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.models.robot import AMRType, Heading, Robot, RobotState
from app.services.conflict_manager import ConflictManager
from app.services.fleet_orchestrator import FleetOrchestrator
from app.services.fleet_state import FleetState
from app.services.planner_adapter import get_planner_adapter
from app.services.reservation_manager import ReservationManager
from app.services.simulation_engine import SimulationEngine
from app.services.task_manager import TaskManager
from app.services.telemetry import Telemetry
from app.websocket.connection_manager import ConnectionManager

cfg = get_settings()
setup_logging(cfg.LOG_LEVEL)
log = logging.getLogger(__name__)


def process_telemetry_frame(
    data: Dict[str, Any],
    fleet_state: FleetState,
    telemetry: Telemetry,
    loop_duration_ms: float = 0.0,
) -> None:
    """Processes incoming TICK_UPDATE, updating fleet_state and real telemetry metrics."""
    if not data or data.get("type") != "TICK_UPDATE":
        return

    tick = data.get("tick", fleet_state.tick)
    fleet_state.tick = tick
    fleet_state.is_running = True

    # 1. Loop processing latency
    if loop_duration_ms > 0:
        telemetry.record_tick(loop_duration_ms)
    elif "last_tick_processing_ms" in data:
        telemetry.record_tick(data["last_tick_processing_ms"])

    # 2. Active conflicts
    conflicts = data.get("active_conflicts", [])
    telemetry.active_conflicts = len(conflicts)

    # 3. Robots, replans & planner latency
    robots_data = data.get("robots", [])
    telemetry.active_robots = len(robots_data)

    planner_latencies = []
    for r_dict in robots_data:
        rid = r_dict.get("id") or r_dict.get("robot_id")
        if not rid:
            continue

        # Increment replans whenever a robot's conflict is non-null or action indicates yield/detour
        if r_dict.get("conflict"):
            telemetry.record_replan()

        p_lat = r_dict.get("planner_latency_ms")
        if p_lat is not None and p_lat > 0:
            planner_latencies.append(p_lat)

        pos_raw = r_dict.get("position")
        if isinstance(pos_raw, dict):
            pos = (int(pos_raw.get("x", 0)), int(pos_raw.get("y", 0)))
        elif isinstance(pos_raw, (list, tuple)):
            pos = (int(pos_raw[0]), int(pos_raw[1]))
        else:
            pos = (int(r_dict.get("x", 0)), int(r_dict.get("y", 0)))
        h_str = r_dict.get("heading", "NORTH")
        try:
            h_enum = Heading(h_str)
        except Exception:
            h_enum = Heading.NORTH

        st_str = r_dict.get("state", "IDLE")
        if isinstance(st_str, str) and "." in st_str:
            st_str = st_str.split(".")[-1]
        try:
            st_enum = RobotState(st_str)
        except Exception:
            st_enum = RobotState.IDLE

        rt_str = r_dict.get("robot_type", "GOODS_TO_PERSON")
        try:
            rt_enum = AMRType(rt_str)
        except Exception:
            rt_enum = AMRType.GOODS_TO_PERSON

        if rid not in fleet_state.robots:
            fleet_state.robots[rid] = Robot(
                robot_id=rid,
                x=pos[0],
                y=pos[1],
                heading=h_enum,
                state=st_enum,
                battery_pct=r_dict.get("battery", 100.0),
                current_task_id=r_dict.get("current_task_id"),
                priority_score=r_dict.get("priority_score", 0),
                last_updated_tick=tick,
                robot_type=rt_enum,
            )
        else:
            rob = fleet_state.robots[rid]
            rob.position = pos
            rob.heading = h_enum
            rob.state = st_enum
            rob.robot_type = rt_enum
            rob.battery_pct = r_dict.get("battery", rob.battery_pct)
            rob.priority_score = r_dict.get("priority_score", rob.priority_score)
            rob.wait_ticks_so_far = r_dict.get("wait_ticks_so_far", rob.wait_ticks_so_far)
            rob.current_task_id = r_dict.get("current_task_id", rob.current_task_id)
            rob.last_updated_tick = tick

    if planner_latencies:
        avg_planner = sum(planner_latencies) / len(planner_latencies)
        telemetry.record_planner(avg_planner)


def _clean_stale_udp_ports(ports: range | list = range(9001, 9011)) -> None:
    """Terminates any stale/zombie processes holding AMR UDP ports on Windows."""
    if sys.platform != "win32":
        return
    import subprocess
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "udp"], text=True)
        my_pid = os.getpid()
        killed = set()
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4 and parts[0].upper() == "UDP":
                addr = parts[1]
                pid_str = parts[-1]
                for p in ports:
                    if f":{p}" in addr:
                        try:
                            pid = int(pid_str)
                            if pid != my_pid and pid not in killed and pid > 0:
                                subprocess.run(
                                    ["taskkill", "/F", "/PID", str(pid)],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                                killed.add(pid)
                        except Exception:
                            pass
        if killed:
            time.sleep(0.3)
    except Exception:
        pass


def _is_udp_port_bound(port: int = 9001, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def _is_external_fleet_active() -> bool:
    """Checks whether an external fleet is genuinely running and actively publishing telemetry."""
    from app.services.telemetry_bus import TELEMETRY_FILE
    if not _is_udp_port_bound(9001):
        return False
    if not TELEMETRY_FILE.exists():
        return False
    try:
        mtime = TELEMETRY_FILE.stat().st_mtime
        return (time.time() - mtime) < 2.5
    except Exception:
        return False


def run_startup_preflight() -> None:
    """
    Tiny preflight check before starting backend:
    1. Python version >= 3.11
    2. Required imports (fastapi, uvicorn, cryptography, pydantic, numpy, networkx, scipy)
    3. Port 8000 free (or bound by server process)
    4. UDP 9001+/9601-9603 free (cleaned of stale zombies)
    5. data/ writable
    """
    if sys.version_info < (3, 11):
        msg = f"[PREFLIGHT FATAL] Python 3.11+ required. Found: {sys.version}"
        log.critical(msg)
        raise RuntimeError(msg)

    import importlib
    required_pkgs = ["fastapi", "uvicorn", "cryptography", "pydantic", "numpy", "networkx", "scipy"]
    for pkg in required_pkgs:
        try:
            importlib.import_module(pkg)
        except ImportError as exc:
            msg = f"[PREFLIGHT FATAL] Required package '{pkg}' missing: {exc}. Run: pip install -r requirements.txt"
            log.critical(msg)
            raise RuntimeError(msg)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            res = s.connect_ex(("127.0.0.1", 8000))
            if res == 0:
                log.info("[PREFLIGHT] Port 8000 is active (bound by server).")
            else:
                log.info("[PREFLIGHT] Port 8000 is free and available.")
    except Exception as exc:
        log.warning("[PREFLIGHT] Port 8000 check skipped: %s", exc)

    _clean_stale_udp_ports(range(9001, 9011))
    _clean_stale_udp_ports([9601, 9602, 9603])

    from pathlib import Path
    data_dir = Path(__file__).resolve().parents[3] / "data"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        test_file = data_dir / ".preflight_write_test"
        test_file.write_text("ok", encoding="utf-8")
        if test_file.exists():
            test_file.unlink()
    except Exception as exc:
        msg = f"[PREFLIGHT FATAL] Directory '{data_dir}' is not writable: {exc}"
        log.critical(msg)
        raise RuntimeError(msg)

    log.info("[PREFLIGHT] Environment preflight passed: Python %s, packages, ports & data/ verified.", sys.version.split()[0])


# ─────────────────────────────────────────────────────────────────────────────
# Application Lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize all services on startup, tear down on shutdown.
    Services are stored in app.state for dependency injection via Request.
    """
    run_startup_preflight()
    log.info("Initializing SIH2026 simulation backend...")

    # ── Core services ─────────────────────────────────────────────────────────
    fleet_state = FleetState()
    reservation_manager = ReservationManager()
    task_manager = TaskManager()
    from app.services.order_manager import OrderManager
    order_manager = OrderManager()
    telemetry = Telemetry()
    telemetry.tick_ms_configured = cfg.SIM_TICK_MS
    connection_manager = ConnectionManager(max_queue=cfg.WS_MAX_QUEUE)
    planner = get_planner_adapter()

    from app.services.telemetry_bus import read_latest_telemetry
    init_data = read_latest_telemetry()
    if init_data:
        process_telemetry_frame(init_data, fleet_state, telemetry)

    conflict_manager = ConflictManager(
        reservation_manager=reservation_manager,
        conflict_radius=cfg.CONFLICT_RADIUS,
    )

    engine = SimulationEngine(
        fleet_state=fleet_state,
        reservation_manager=reservation_manager,
        task_manager=task_manager,
        conflict_manager=conflict_manager,
        connection_manager=connection_manager,
        telemetry=telemetry,
        planner=planner,
    )

    # ── Load initial map configuration ─────────────────────────────────────────
    current_map_path = ROOT_DIR / "maps" / "current_map.json"
    test_map_path = ROOT_DIR / "maps" / "test_map.json"
    active_map_dict = None
    if current_map_path.exists():
        try:
            with open(current_map_path, "r", encoding="utf-8") as f:
                active_map_dict = json.load(f)
        except Exception:
            pass
    if active_map_dict is None and test_map_path.exists():
        try:
            with open(test_map_path, "r", encoding="utf-8") as f:
                active_map_dict = json.load(f)
        except Exception:
            pass

    if active_map_dict:
        from app.models.world import build_world_from_map_dict, set_active_world
        init_world, _ = build_world_from_map_dict(active_map_dict)
        fleet_state.world = init_world
        set_active_world(init_world, active_map_dict)

    # ── SPOF Hardening: Recover in-flight jobs from write-ahead journal ────────
    from app.services.inventory_ledger import InventoryLedger
    inventory_ledger = InventoryLedger()
    if active_map_dict:
        inventory_ledger.seed_from_map(active_map_dict, fleet_state.world)
    else:
        inventory_ledger.seed_default_inventory(fleet_state.world)

    from app.services.job_journal import JobJournal
    job_journal = JobJournal()
    recovered_jobs = job_journal.recover_uncompleted_jobs()
    auto_recover = os.environ.get("ENABLE_AUTO_RECOVERY", "0") == "1"

    if recovered_jobs and auto_recover:
        log.info("[SPOF RECOVERY] Auto-recovery enabled: replaying %d uncompleted jobs from journal...", len(recovered_jobs))
        for r_job in recovered_jobs:
            try:
                pickup = r_job.get("pickup") or [2, 2]
                dropoff = r_job.get("dropoff") or [27, 27]
                t = task_manager.create_task(
                    pickup_x=pickup[0],
                    pickup_y=pickup[1],
                    dropoff_x=dropoff[0],
                    dropoff_y=dropoff[1],
                    urgency=r_job.get("urgency", 3),
                    current_tick=fleet_state.tick,
                )
                t.task_id = r_job["job_id"]
                if r_job.get("assigned_robot_id"):
                    t.assigned_robot_id = r_job["assigned_robot_id"]
                fleet_state.queue_task(t)
            except Exception as ex:
                log.warning("[SPOF RECOVERY] Error replaying job %s: %s", r_job.get("job_id"), ex)
        uncompleted_pending = []
    else:
        if recovered_jobs:
            log.info(
                "[SPOF RECOVERY] Found %d uncompleted jobs from previous session. "
                "Startup recovery is opt-in (default OFF). Pending user action via API/UI.",
                len(recovered_jobs),
            )
        uncompleted_pending = recovered_jobs

    # ── Autonomous Decentralized Fleet Orchestrator ───────────────────────────
    orchestrator = None
    spawn_enabled = os.environ.get("SPAWN_FLEET_ORCHESTRATOR", "1") == "1"

    if _is_external_fleet_active():
        fleet_mode = "attached_to_existing_fleet"
        log.info(
            "[FLEET STARTUP] Mode: ATTACHED TO EXISTING FLEET (UDP port 9001 active with live telemetry). "
            "Operating as pure Telemetry Viewer."
        )
    elif spawn_enabled:
        if _is_udp_port_bound(9001):
            log.info("[FLEET STARTUP] Cleaning stale zombie processes holding UDP ports 9001-9010...")
            _clean_stale_udp_ports()

        fleet_mode = "spawned_new_fleet"
        from app.services.fleet_orchestrator import FleetOrchestrator
        orchestrator = FleetOrchestrator(
            map_data=active_map_dict,
            tick_interval_s=cfg.SIM_TICK_MS / 1000.0,
            max_ticks=0,
        )
        orchestrator.reset_logs()

        # Populate initial robots into fleet_state
        for c in orchestrator.robots_config:
            rid = c["robot_id"]
            from app.models.robot import Robot, AMRType
            rtype_str = c.get("robot_type", "GOODS_TO_PERSON")
            try:
                rtype_enum = AMRType(rtype_str)
            except Exception:
                rtype_enum = AMRType.GOODS_TO_PERSON
            r = Robot(
                robot_id=rid,
                x=c["start"][0],
                y=c["start"][1],
                heading=Heading.NORTH,
                state=RobotState.IDLE,
                battery_pct=100.0,
                current_task_id=None,
                priority_score=0,
                last_updated_tick=0,
                robot_type=rtype_enum,
            )
            fleet_state.robots[rid] = r

        log.info(
            "[FLEET STARTUP] Mode: SPAWNED NEW FLEET (Spawning %d autonomous AMR OS processes on ports 9001+)...",
            len(orchestrator.robots_config),
        )
        orchestrator.start()
    else:
        fleet_mode = "no_fleet_detected_robots_not_running"
        log.warning(
            "[FLEET STARTUP] Mode: NO FLEET DETECTED (UDP port 9001 free and SPAWN_FLEET_ORCHESTRATOR=0). "
            "Robots are NOT running."
        )

    # ── Store in app.state for route handlers ─────────────────────────────────
    app.state.fleet_state = fleet_state
    app.state.reservation_manager = reservation_manager
    app.state.task_manager = task_manager
    app.state.conflict_manager = conflict_manager
    app.state.connection_manager = connection_manager
    app.state.telemetry = telemetry
    app.state.engine = engine
    app.state.orchestrator = orchestrator
    app.state.telemetry_streaming_paused = False
    app.state.fleet_mode = fleet_mode
    app.state.job_journal = job_journal
    app.state.inventory_ledger = inventory_ledger
    app.state.uncompleted_jobs_pending = uncompleted_pending
    app.state.order_manager = order_manager

    # ── Decentralized Fleet Telemetry Forwarder (Pure Telemetry Viewer) ────────
    from app.services.telemetry_bus import read_latest_telemetry
    from app.websocket.delta_encoder import FleetDeltaEncoder
    import asyncio
    import json

    delta_encoder = FleetDeltaEncoder()

    async def _telemetry_forwarder():
        """Reads updates from the independent robot processes and broadcasts them with delta encoding."""
        last_tick = -1
        disconnect_time: Optional[float] = None
        auto_pause = os.environ.get("AUTO_PAUSE_ON_DISCONNECT", "0") == "1"

        while True:
            try:
                cur_orch = getattr(app.state, "orchestrator", None)
                clients = len(connection_manager._connections)
                telemetry.connected_clients = clients

                # Optional auto-pause simulation processes when no browser tabs are open for > 60s
                if auto_pause and cur_orch is not None and fleet_state.is_running:
                    if clients == 0:
                        if disconnect_time is None:
                            disconnect_time = time.time()
                        elif time.time() - disconnect_time > 60.0 and not cur_orch.is_paused():
                            log.info("Zero active dashboard clients for 60s. Auto-pausing fleet processes...")
                            cur_orch.pause()
                    else:
                        disconnect_time = None
                        if cur_orch.is_paused() and not getattr(app.state, "telemetry_streaming_paused", False):
                            log.info("Dashboard client connected. Auto-resuming fleet processes...")
                            cur_orch.resume()
                else:
                    disconnect_time = None

                is_orch_paused = cur_orch.is_paused() if cur_orch is not None else False
                if not getattr(app.state, "telemetry_streaming_paused", False) and not is_orch_paused:
                    t_start = time.perf_counter()
                    data = read_latest_telemetry()
                    if data and data.get("tick", -1) != last_tick:
                        last_tick = data["tick"]
                        proc_ms = (time.perf_counter() - t_start) * 1000.0
                        process_telemetry_frame(data, fleet_state, telemetry, loop_duration_ms=proc_ms)
                        data["tick_ms"] = cfg.SIM_TICK_MS
                        # Synchronize task state, active obstacles, metrics, and fleet status onto the TICK_UPDATE frame
                        for r_tel in fleet_state.robots.values():
                            if r_tel.current_task_id:
                                t_obj = task_manager.get_task(r_tel.current_task_id)
                                if t_obj and t_obj.assigned_robot_id != r_tel.robot_id:
                                    task_manager.record_claim(r_tel.current_task_id, r_tel.robot_id, 40, fleet_state.tick)

                        data["tasks"] = [
                            {
                                "task_id": t.task_id,
                                "pickup": {"x": t.pickup_x, "y": t.pickup_y},
                                "dropoff": {"x": t.dropoff_x, "y": t.dropoff_y},
                                "urgency": t.urgency,
                                "status": t.status.value,
                                "assigned_robot_id": t.assigned_robot_id,
                                "created_tick": t.created_tick,
                                "task_type": t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type),
                                "target_shelf_id": getattr(t, "target_shelf_id", None),
                                "return_to_home": getattr(t, "return_to_home", True),
                                "lease_expires_tick": getattr(t, "lease_expires_tick", None),
                                "unclaimed_reason": getattr(t, "unclaimed_reason", None),
                            }
                            for t in task_manager.all_tasks().values()
                        ]

                        data["temporary_obstacles"] = [
                            {
                                "obstacle_id": obs.obstacle_id,
                                "position": {"x": obs.x, "y": obs.y},
                                "created_tick": obs.created_tick,
                                "expires_at_tick": obs.expires_at_tick,
                            }
                            for obs in fleet_state.temp_obstacles.values()
                            if obs.is_active(fleet_state.tick)
                        ]
                        data["metrics"] = telemetry.snapshot()
                        has_active_tasks = any(
                            t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, "COMPLETED", "FAILED")
                            for t in task_manager.all_tasks().values()
                        ) or any(
                            r.current_task_id is not None or r.state in (RobotState.EN_ROUTE_PICKUP, RobotState.PICKING, RobotState.EN_ROUTE_DROPOFF, RobotState.DROPPING, RobotState.LIFTING, RobotState.LOWERING)
                            for r in fleet_state.robots.values()
                        )
                        data["fleet_status"] = {
                            "running": fleet_state.is_running,
                            "mode": getattr(app.state, "fleet_mode", "spawned_new_fleet"),
                            "tick": fleet_state.tick,
                            "armed_state": ("RUNNING" if has_active_tasks else "ARMED — waiting for tasks") if fleet_state.is_running else "STOPPED",
                            "pending_recovery_count": len(getattr(app.state, "uncompleted_jobs_pending", [])),
                        }
                        # Include live inventory ledger snapshot and sortation chutes
                        try:
                            data["inventory"] = [s.to_dict() for s in inventory_ledger.get_all_shelves()]
                            data["sortation_chutes"] = fleet_state.world.sortation_chutes
                        except Exception:
                            pass

                        # Update OrderManager lifecycle tracking (Step 5)
                        try:
                            for r in fleet_state.robots.values():
                                if r.carrying_pod_id:
                                    order_manager.on_pod_lifted(r.carrying_pod_id, r.robot_id, fleet_state.tick)
                            for sid, st in fleet_state.world.pick_stations.items():
                                for carton in st.get("buffer_items", []):
                                    oid = getattr(carton, "order_id", None) or (carton.get("order_id") if isinstance(carton, dict) else None)
                                    if oid:
                                        ord_obj = order_manager.get_order(oid)
                                        if ord_obj:
                                            order_manager.on_at_pick_station(ord_obj.shelf_id or "", sid, fleet_state.tick)
                            for t in task_manager.all_tasks().values():
                                oid = getattr(t, "order_id", None)
                                if oid and t.status in (TaskStatus.COMPLETED, "COMPLETED"):
                                    ttype = str(getattr(t, "task_type", ""))
                                    if "CONSOLIDATE" in ttype:
                                        order_manager.on_shipped(oid, fleet_state.tick)
                                    elif "TRANSFER" in ttype:
                                        dest_zone = getattr(t, "destination_zone", "")
                                        chute_id = fleet_state.world.chute_for_destination(dest_zone)
                                        if chute_id:
                                            order_manager.on_in_chute(oid, chute_id, fleet_state.tick)
                            order_manager.update_stuck_reasons(fleet_state.tick)
                            data["orders"] = [o.to_dict() for o in order_manager.all_orders()]
                        except Exception as e:
                            log.debug("Order tracking update error: %s", e)

                        full_json = json.dumps(data, separators=(",", ":"))
                        connection_manager.latest_baseline_json = full_json

                        # Forward synchronized TICK_UPDATE or compact TICK_DELTA payload to WebSocket clients
                        if clients > 0:
                            delta = delta_encoder.compute_delta(data)
                            delta_json = json.dumps(delta, separators=(",", ":"))
                            await connection_manager.broadcast_telemetry(full_json, delta_json)
            except Exception as e:
                log.debug("Telemetry forwarder error: %s", e)
            await asyncio.sleep(0.04)

    forwarder_task = asyncio.create_task(_telemetry_forwarder(), name="telemetry_forwarder")

    async def _halow_receiver():
        """Listens on UDP port 9099 for simulated 802.11ah WiFi HaLow packets."""
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.bind(("127.0.0.1", 9099))
        except Exception as e:
            log.warning("Could not bind HaLow receiver on port 9099: %s", e)
            return

        from app.security.hmac_envelope import verify_envelope

        while True:
            try:
                data, _ = await loop.sock_recv(sock, 4096)
                if data:
                    raw_str = data.decode("utf-8")
                    envelope = json.loads(raw_str)
                    if verify_envelope(envelope):
                        payload = envelope.get("payload", {})
                        m_type = payload.get("type", "")

                        if m_type == "TASK_ANNOUNCEMENT":
                            t_dict = payload.get("task", {})
                            t_id = t_dict.get("task_id")
                            oid = t_dict.get("order_id")
                            if t_id and oid:
                                order_manager.link_task(t_id, oid)
                            if t_id and t_id not in task_manager._tasks:
                                from app.models.task import Task, TaskType, TaskStatus
                                p_pos = t_dict.get("pickup", [0, 0])
                                d_pos = t_dict.get("dropoff", [0, 0])
                                try:
                                    t_type_enum = TaskType(t_dict.get("task_type", "STANDARD"))
                                except Exception:
                                    t_type_enum = TaskType.STANDARD
                                auto_t = Task(
                                    task_id=t_id,
                                    pickup_x=p_pos[0],
                                    pickup_y=p_pos[1],
                                    dropoff_x=d_pos[0],
                                    dropoff_y=d_pos[1],
                                    urgency=int(t_dict.get("urgency", 3)),
                                    created_tick=int(payload.get("tick", fleet_state.tick)),
                                    status=TaskStatus.ANNOUNCED,
                                    task_type=t_type_enum,
                                    destination_zone=t_dict.get("destination_zone"),
                                    pick_station_id=t_dict.get("pick_station_id"),
                                    order_id=oid,
                                    destination_gate=t_dict.get("destination_gate"),
                                )
                                task_manager._tasks[t_id] = auto_t

                        elif m_type == "TASK_CLAIM":
                            t_id = payload.get("task_id")
                            w_id = payload.get("winner_id") or payload.get("robot_id")
                            l_ticks = int(payload.get("lease_ticks", 40))
                            c_tick = int(payload.get("tick", fleet_state.tick))
                            oid = payload.get("order_id")
                            t_type = payload.get("task_type")
                            if t_id and oid:
                                order_manager.link_task(t_id, oid)
                            if t_id and w_id:
                                task_manager.record_claim(t_id, w_id, l_ticks, c_tick)
                                t_obj = task_manager._tasks.get(t_id)
                                if not t_type:
                                    t_type = str(getattr(t_obj, "task_type", "")) if t_obj else ""
                                order_manager.on_task_claimed(t_id, w_id, t_type, c_tick)

                        elif m_type == "TASK_LEASE_HEARTBEAT":
                            t_id = payload.get("task_id")
                            r_id = payload.get("robot_id") or payload.get("sender_id")
                            l_ticks = int(payload.get("lease_ticks", 40))
                            c_tick = int(payload.get("tick", fleet_state.tick))
                            if t_id and r_id:
                                task_manager.record_lease_heartbeat(t_id, r_id, l_ticks, c_tick)

                        elif m_type == "TASK_COMPLETED":
                            t_id = payload.get("task_id")
                            r_id = payload.get("robot_id") or payload.get("sender_id")
                            c_tick = int(payload.get("tick", fleet_state.tick))
                            oid = payload.get("order_id")
                            ttype = str(payload.get("task_type") or "")
                            if t_id:
                                task_manager.mark_completed_by_id(t_id, r_id, c_tick)
                                t_obj = task_manager._tasks.get(t_id)
                                if not oid and t_obj:
                                    oid = getattr(t_obj, "order_id", None)
                                if not ttype and t_obj:
                                    ttype = str(getattr(t_obj, "task_type", ""))
                                if oid:
                                    if "CONSOLIDATE" in t_id or "CONSOLIDATE" in ttype:
                                        order_manager.on_shipped(oid, c_tick)
                                    elif "TRANSFER" in t_id or "TRANSFER" in ttype:
                                        dest_zone = payload.get("destination_zone") or getattr(t_obj, "destination_zone", "")
                                        chute_id = fleet_state.world.chute_for_destination(dest_zone)
                                        if chute_id:
                                            order_manager.on_in_chute(oid, chute_id, c_tick)

                        elif m_type in ("INVENTORY_UPDATE", "INVENTORY_SYNC") or "shelf_id" in payload:
                            sync_msg = {
                                "type": "INVENTORY_SYNC",
                                "channel": "HALOW",
                                "shelf_id": payload.get("shelf_id"),
                                "x": payload.get("x", 0),
                                "y": payload.get("y", 0),
                                "current_box_count": payload.get("current_box_count", 0),
                                "sku_manifest": payload.get("sku_manifest", {}),
                                "confidence": payload.get("confidence", 1.0),
                                "last_audited_tick": payload.get("tick", 0),
                                "last_audited_by": payload.get("source_robot_id") or payload.get("sender_id"),
                                "source": payload.get("source", "audit_scan"),
                                "timestamp_ms": int(time.time() * 1000),
                            }
                            if len(connection_manager._connections) > 0:
                                await connection_manager.broadcast_json(sync_msg)
            except asyncio.CancelledError:
                break
            except Exception as ex:
                log.debug("HaLow receiver error: %s", ex)
            await asyncio.sleep(0.01)
        sock.close()

    halow_task = asyncio.create_task(_halow_receiver(), name="halow_receiver")

    from app.services.task_manager import get_fleet_peer_ports

    async def _pending_task_dispatcher():
        """Periodically evaluates task leases, unassigned tasks, and re-announces to available robots."""
        while True:
            try:
                p_ports = get_fleet_peer_ports(getattr(app.state, "orchestrator", None))
                task_manager.check_leases_and_unclaimed(fleet_state.tick, peer_ports=p_ports)
            except Exception as e:
                log.debug("Pending task dispatcher error: %s", e)
            await asyncio.sleep(0.5)

    dispatcher_task = asyncio.create_task(_pending_task_dispatcher(), name="pending_task_dispatcher")


    log.info(
        "Backend ready as Pure Telemetry Viewer. Grid=%dx%d Fleet=%d Tick=%dms",
        cfg.GRID_WIDTH, cfg.GRID_HEIGHT, cfg.FLEET_SIZE, cfg.SIM_TICK_MS,
    )

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Shutting down telemetry viewer...")
    forwarder_task.cancel()
    dispatcher_task.cancel()
    halow_task.cancel()
    try:
        await forwarder_task
    except asyncio.CancelledError:
        pass
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass
    try:
        await halow_task
    except asyncio.CancelledError:
        pass

    if fleet_state.is_running and engine._running:
        await engine.pause()
    if orchestrator is not None:
        orchestrator.stop()
    if hasattr(job_journal, "close"):
        job_journal.close()
    log.info("Backend shutdown complete.")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SIH2026 — Edge-AI Fleet Coordination Backend",
    description=(
        "Backend & Edge Simulation Broker for SIH26123. "
        "Runs the authoritative simulation clock for a 30×30 warehouse with 10 AMRs. "
        "SCHEMA.md is the single source of truth."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Allow all origins for development. Restrict in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(tasks.router)
app.include_router(tasks.job_router)
app.include_router(simulation.router)
app.include_router(chaos.router)
app.include_router(robots.router)
app.include_router(websocket.router)
app.include_router(world_router)
app.include_router(map_router)


# ── Product Catalog ───────────────────────────────────────────────────────────
@app.get("/api/catalog", tags=["Catalog"])
def get_product_catalog(request: Request) -> List[Dict[str, Any]]:
    """Returns the live product catalog and aggregated in-stock quantities."""
    from app.models.world import get_active_map_data
    ledger = getattr(request.app.state, "inventory_ledger", None)
    active_data = get_active_map_data() or {}
    map_catalog = active_data.get("catalog", [])
    default_catalog = [
        {"sku": "SKU-A10", "name": "Standard Bolt Pack", "weight_kg": 2.5},
        {"sku": "SKU-A20", "name": "Precision Bearings", "weight_kg": 1.8},
        {"sku": "SKU-B10", "name": "Hydraulic Seals", "weight_kg": 0.9},
        {"sku": "SKU-B20", "name": "Motor Brushes", "weight_kg": 1.2},
        {"sku": "SKU-C10", "name": "Control Cables", "weight_kg": 3.1},
        {"sku": "SKU-C20", "name": "Optical Sensors", "weight_kg": 0.5},
        {"sku": "SKU-D10", "name": "Lithium Battery Cells", "weight_kg": 4.0},
        {"sku": "SKU-D20", "name": "Terminal Relays", "weight_kg": 1.1},
        {"sku": "SKU-E10", "name": "Industrial Fasteners", "weight_kg": 2.2},
        {"sku": "SKU-E20", "name": "Servo Couplers", "weight_kg": 1.4},
        {"sku": "SKU-F10", "name": "Fiber Optic Patch", "weight_kg": 0.3},
        {"sku": "SKU-F20", "name": "Pneumatic Valve Kit", "weight_kg": 2.8},
    ]
    catalog = map_catalog if map_catalog else default_catalog
    stock_counts: Dict[str, int] = {}
    if ledger:
        for shelf in ledger.get_all_shelves():
            for sku, qty in shelf.sku_manifest.items():
                stock_counts[sku] = stock_counts.get(sku, 0) + int(qty)

    result = []
    for item in catalog:
        sku = item["sku"]
        result.append({
            "sku": sku,
            "name": item.get("name", sku),
            "weight_kg": float(item.get("weight_kg", 2.0)),
            "total_stock": stock_counts.get(sku, 0),
        })
    return result


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health() -> dict:
    fleet = app.state.fleet_state
    fleet_mode = getattr(app.state, "fleet_mode", "unknown")
    orchestrator = getattr(app.state, "orchestrator", None)
    is_paused = orchestrator.is_paused() if orchestrator else False
    mode_descriptions = {
        "spawned_new_fleet": "Spawned new fleet (autonomous AMR processes active)",
        "attached_to_existing_fleet": "Attached to existing fleet (UDP telemetry stream active)",
        "no_fleet_detected_robots_not_running": "No fleet detected, robots not running",
    }
    return {
        "status": "ok",
        "tick": fleet.tick,
        "running": fleet.is_running,
        "is_paused": is_paused,
        "robots": len(fleet.robots),
        "fleet_mode": fleet_mode,
        "fleet_mode_description": mode_descriptions.get(fleet_mode, fleet_mode),
    }


# ── Frontend Visualizer & Health Landing Page ───────────────────────────────────
@app.get("/", include_in_schema=False)
@app.get("/simulator", include_in_schema=False)
async def serve_simulator() -> Response:
    """Serve the primary fleet visualizer status landing page directly from backend."""
    index_file = Path(__file__).resolve().parent / "index.html"
    if index_file.is_file():
        return FileResponse(index_file, media_type="text/html")
    return HTMLResponse(
        """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>SIH2026 Fleet Telemetry Backend</title>
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 48px; }
                .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 32px; max-width: 640px; margin: 0 auto; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.3); }
                h1 { color: #38bdf8; margin-top: 0; font-size: 1.75rem; }
                p { color: #94a3b8; line-height: 1.6; }
                ul { list-style: none; padding: 0; }
                li { margin: 12px 0; }
                a { color: #38bdf8; text-decoration: none; font-weight: 500; }
                a:hover { text-decoration: underline; }
                code { background: #0f172a; padding: 4px 8px; border-radius: 6px; color: #a5f3fc; font-family: monospace; }
                .status-badge { display: inline-block; background: #065f46; color: #6ee7b7; padding: 4px 10px; border-radius: 9999px; font-size: 0.85rem; font-weight: 600; margin-bottom: 16px; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="status-badge">ONLINE • PURE TELEMETRY VIEWER</div>
                <h1>SIH2026 Edge-AI Fleet Coordination</h1>
                <p>Autonomous AMR nodes run in independent OS processes, communicating peer-to-peer over UDP sockets with cryptographic HMAC verification and ReplayGuard.</p>
                <ul>
                    <li>📄 <strong>Interactive API Docs:</strong> <a href="/docs">/docs</a></li>
                    <li>🩺 <strong>System Health:</strong> <a href="/health">/health</a></li>
                    <li>📡 <strong>Live Telemetry Stream:</strong> <code>ws://localhost:8000/ws/fleet</code></li>
                </ul>
            </div>
        </body>
        </html>
        """
    )


