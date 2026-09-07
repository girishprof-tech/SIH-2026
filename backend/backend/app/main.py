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
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from app.api import chaos, robots, simulation, tasks, websocket
from app.api.chaos_and_world import router as world_router
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


# ─────────────────────────────────────────────────────────────────────────────
# Application Lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize all services on startup, tear down on shutdown.
    Services are stored in app.state for dependency injection via Request.
    """
    log.info("Initializing SIH2026 simulation backend...")

    # ── Core services ─────────────────────────────────────────────────────────
    fleet_state = FleetState()
    reservation_manager = ReservationManager()
    task_manager = TaskManager()
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

    def _is_udp_port_bound(port: int = 9001, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    except Exception:
                        pass
                s.bind((host, port))
                return False
            except OSError:
                return True

    # ── Autonomous Decentralized Fleet Orchestrator ───────────────────────────
    orchestrator = None
    fleet_already_running = _is_udp_port_bound(9001)
    spawn_enabled = os.environ.get("SPAWN_FLEET_ORCHESTRATOR", "1") == "1"

    if fleet_already_running:
        fleet_mode = "attached_to_existing_fleet"
        log.info(
            "[FLEET STARTUP] Mode: ATTACHED TO EXISTING FLEET (UDP port 9001 bound by existing process). "
            "Operating as pure Telemetry Viewer."
        )
    elif spawn_enabled:
        fleet_mode = "spawned_new_fleet"
        log.info(
            "[FLEET STARTUP] Mode: SPAWNED NEW FLEET (Spawning %d autonomous AMR OS processes on ports 9001+)...",
            cfg.FLEET_SIZE,
        )
        orchestrator = FleetOrchestrator(tick_interval_s=cfg.SIM_TICK_MS / 1000.0, max_ticks=0)
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

    # ── Decentralized Fleet Telemetry Forwarder (Pure Telemetry Viewer) ────────
    from app.services.telemetry_bus import read_latest_telemetry
    import asyncio

    async def _telemetry_forwarder():
        """Reads updates from the independent robot processes and broadcasts them."""
        last_tick = -1
        disconnect_time: Optional[float] = None
        auto_pause = os.environ.get("AUTO_PAUSE_ON_DISCONNECT", "1") == "1"

        while True:
            try:
                clients = len(connection_manager._connections)
                telemetry.connected_clients = clients

                # Auto-pause simulation processes when no browser tabs are open
                if auto_pause and orchestrator is not None:
                    if clients == 0:
                        if disconnect_time is None:
                            disconnect_time = time.time()
                        elif time.time() - disconnect_time > 3.0 and not orchestrator.is_paused():
                            log.info("Zero active dashboard clients for 3s. Auto-pausing fleet processes...")
                            orchestrator.pause()
                    else:
                        disconnect_time = None
                        if orchestrator.is_paused() and not getattr(app.state, "telemetry_streaming_paused", False):
                            log.info("Dashboard client connected. Auto-resuming fleet processes...")
                            orchestrator.resume()

                if not getattr(app.state, "telemetry_streaming_paused", False) and not (orchestrator and orchestrator.is_paused()):
                    t_start = time.perf_counter()
                    data = read_latest_telemetry()
                    if data and data.get("tick", -1) != last_tick:
                        last_tick = data["tick"]
                        proc_ms = (time.perf_counter() - t_start) * 1000.0
                        process_telemetry_frame(data, fleet_state, telemetry, loop_duration_ms=proc_ms)

                        # Synchronize task state, active obstacles, metrics, and fleet status onto the TICK_UPDATE frame
                        data["tasks"] = [
                            {
                                "task_id": t.task_id,
                                "pickup": {"x": t.pickup_x, "y": t.pickup_y},
                                "dropoff": {"x": t.dropoff_x, "y": t.dropoff_y},
                                "urgency": t.urgency,
                                "status": t.status.value,
                                "assigned_robot_id": t.assigned_robot_id,
                                "created_tick": t.created_tick,
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
                        data["fleet_status"] = {
                            "running": fleet_state.is_running,
                            "mode": getattr(app.state, "fleet_mode", "spawned_new_fleet"),
                            "tick": fleet_state.tick,
                        }

                        # Forward synchronized TICK_UPDATE payload to WebSocket clients
                        if clients > 0:
                            await connection_manager.broadcast_json(data)
            except Exception as e:
                log.debug("Telemetry forwarder error: %s", e)
            await asyncio.sleep(0.04)

    forwarder_task = asyncio.create_task(_telemetry_forwarder(), name="telemetry_forwarder")

    from app.services.task_manager import get_fleet_peer_ports

    async def _pending_task_dispatcher():
        """Periodically scans pending tasks and dispatches to available idle robots."""
        while True:
            try:
                pending = task_manager.pending_tasks()
                if pending:
                    p_ports = get_fleet_peer_ports(getattr(app.state, "orchestrator", None))
                    for t in pending:
                        assigned = task_manager.dispatch_to_fleet(t, peer_ports=p_ports)
                        if assigned:
                            log.info("DISPATCH_RETRY: Pending task %s dispatched to %s", t.task_id, assigned)
            except Exception as e:
                log.debug("Pending task dispatcher error: %s", e)
            await asyncio.sleep(1.0)

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
    try:
        await forwarder_task
    except asyncio.CancelledError:
        pass
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass

    if fleet_state.is_running and engine._running:
        await engine.pause()
    if orchestrator is not None:
        orchestrator.stop()
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


