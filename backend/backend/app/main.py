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
from app.services.conflict_manager import ConflictManager
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

    # ── Store in app.state for route handlers ─────────────────────────────────
    app.state.fleet_state = fleet_state
    app.state.reservation_manager = reservation_manager
    app.state.task_manager = task_manager
    app.state.conflict_manager = conflict_manager
    app.state.connection_manager = connection_manager
    app.state.telemetry = telemetry
    app.state.engine = engine

    log.info(
        "Backend ready. Grid=%dx%d Fleet=%d Tick=%dms Planner=%s",
        cfg.GRID_WIDTH, cfg.GRID_HEIGHT, cfg.FLEET_SIZE,
        cfg.SIM_TICK_MS, planner.name,
    )

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("Shutting down simulation engine...")
    if fleet_state.is_running:
        await engine.pause()
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
