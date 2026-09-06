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
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chaos, robots, simulation, tasks, websocket
from app.api.chaos_and_world import router as world_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.models.robot import Heading, Robot
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

    # ── Autonomous Decentralized Fleet Orchestrator ───────────────────────────
    orchestrator = FleetOrchestrator(tick_interval_s=cfg.SIM_TICK_MS / 1000.0)
    orchestrator.start()

    # ── Store in app.state for route handlers ─────────────────────────────────
    app.state.fleet_state = fleet_state
    app.state.reservation_manager = reservation_manager
    app.state.task_manager = task_manager
    app.state.conflict_manager = conflict_manager
    app.state.connection_manager = connection_manager
    app.state.telemetry = telemetry
    app.state.engine = engine
    app.state.orchestrator = orchestrator

    # ── Decentralized Fleet Telemetry Forwarder (Pure Telemetry Viewer) ────────
    from app.services.telemetry_bus import read_latest_telemetry
    import asyncio

    async def _telemetry_forwarder():
        """Reads updates from the independent robot processes and broadcasts them."""
        last_tick = -1
        while True:
            try:
                data = read_latest_telemetry()
                if data and data.get("tick", -1) != last_tick:
                    last_tick = data["tick"]
                    fleet_state.tick = last_tick
                    fleet_state.is_running = True
                    # Update robot states for REST endpoints
                    for r_dict in data.get("robots", []):
                        rid = r_dict.get("id") or r_dict.get("robot_id")
                        if not rid:
                            continue
                        pos = tuple(r_dict.get("position", [r_dict.get("x", 0), r_dict.get("y", 0)]))
                        h_str = r_dict.get("heading", "NORTH")
                        try:
                            h_enum = Heading(h_str)
                        except Exception:
                            h_enum = Heading.NORTH

                        if rid not in fleet_state.robots:
                            fleet_state.robots[rid] = Robot(
                                robot_id=rid,
                                position=pos,
                                heading=h_enum,
                                battery_pct=r_dict.get("battery", 100.0),
                            )
                        else:
                            rob = fleet_state.robots[rid]
                            rob.position = pos
                            rob.heading = h_enum
                            rob.battery_pct = r_dict.get("battery", rob.battery_pct)
                            rob.priority_score = r_dict.get("priority_score", rob.priority_score)
                            rob.wait_ticks_so_far = r_dict.get("wait_ticks_so_far", rob.wait_ticks_so_far)

                    # Forward TICK_UPDATE payload to WebSocket clients
                    await connection_manager.broadcast_json(data)
            except Exception as e:
                log.debug("Telemetry forwarder error: %s", e)
            await asyncio.sleep(0.04)

    forwarder_task = asyncio.create_task(_telemetry_forwarder(), name="telemetry_forwarder")

    log.info(
        "Backend ready as Pure Telemetry Viewer. Grid=%dx%d Fleet=%d Tick=%dms",
        cfg.GRID_WIDTH, cfg.GRID_HEIGHT, cfg.FLEET_SIZE, cfg.SIM_TICK_MS,
    )

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Shutting down telemetry viewer...")
    forwarder_task.cancel()
    try:
        await forwarder_task
    except asyncio.CancelledError:
        pass

    if fleet_state.is_running and engine._running:
        await engine.pause()
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
app.include_router(simulation.router)
app.include_router(chaos.router)
app.include_router(robots.router)
app.include_router(websocket.router)
app.include_router(world_router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health() -> dict:
    fleet = app.state.fleet_state
    return {
        "status": "ok",
        "tick": fleet.tick,
        "running": fleet.is_running,
        "robots": len(fleet.robots),
    }


# ── Frontend Visualizer & Asset Integration ───────────────────────────────────
from fastapi.responses import FileResponse
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parents[3]
_HTML_FILE = _ROOT_DIR / "unified_warehouse_simulator.html"
_JS_DATA_FILE = _ROOT_DIR / "scenarios_data.js"
_JSON_DATA_FILE = _ROOT_DIR / "scenarios_data.json"


@app.get("/", include_in_schema=False)
@app.get("/simulator", include_in_schema=False)
async def serve_simulator() -> FileResponse:
    """Serve the primary fleet visualizer frontend directly from backend."""
    if _HTML_FILE.is_file():
        return FileResponse(_HTML_FILE, media_type="text/html")
    return FileResponse(Path(__file__).resolve().parent / "index.html", media_type="text/html")


@app.get("/scenarios_data.js", include_in_schema=False)
async def serve_scenarios_js() -> FileResponse:
    """Serve the pre-computed scenario definitions."""
    if _JS_DATA_FILE.is_file():
        return FileResponse(_JS_DATA_FILE, media_type="application/javascript")
    return FileResponse(_ROOT_DIR / "scenarios_data.js", media_type="application/javascript")


@app.get("/scenarios_data.json", include_in_schema=False)
async def serve_scenarios_json() -> FileResponse:
    """Serve the scenario JSON export."""
    return FileResponse(_JSON_DATA_FILE, media_type="application/json")

