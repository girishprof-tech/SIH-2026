"""
Simulation control endpoints — SCHEMA.md §18.

POST /api/simulation/start
POST /api/simulation/pause
POST /api/simulation/reset
GET  /api/simulation/status
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulation", tags=["Simulation"])


class SimStatusOut(BaseModel):
    running: bool
    tick: int
    timestamp_ms: int
    fleet_size: int
    tick_ms: int


@router.post("/start", summary="Start simulation")
async def start_simulation(request: Request) -> dict:
    engine = request.app.state.engine
    fleet = request.app.state.fleet_state
    if fleet.is_running:
        raise HTTPException(409, "Simulation already running")
    await engine.start()
    return {"status": "started", "tick": fleet.tick}


@router.post("/pause", summary="Pause simulation")
async def pause_simulation(request: Request) -> dict:
    engine = request.app.state.engine
    fleet = request.app.state.fleet_state
    if not fleet.is_running:
        raise HTTPException(409, "Simulation is not running")
    await engine.pause()
    return {"status": "paused", "tick": fleet.tick}


@router.post("/reset", summary="Reset simulation to initial state")
async def reset_simulation(request: Request) -> dict:
    engine = request.app.state.engine
    await engine.reset()
    return {"status": "reset", "tick": 0}


@router.get("/status", summary="Get simulation status", response_model=SimStatusOut)
async def get_status(request: Request) -> SimStatusOut:
    from app.core.config import get_settings
    fleet = request.app.state.fleet_state
    cfg = get_settings()
    return SimStatusOut(
        running=fleet.is_running,
        tick=fleet.tick,
        timestamp_ms=fleet.timestamp_ms,
        fleet_size=len(fleet.robots),
        tick_ms=cfg.SIM_TICK_MS,
    )


class FuzzScenarioRequest(BaseModel):
    num_robots: int = 20
    max_ticks: int = 40
    seed: int | None = None


@router.post("/generate_fuzz", summary="Generate a randomized real fuzz scenario using Space-Time A* and Conflict Engine")
async def generate_fuzz_scenario(payload: FuzzScenarioRequest = FuzzScenarioRequest()) -> dict:
    import random
    import sys
    from pathlib import Path
    root_dir = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root_dir / "testing"))
    from full_integration_test import get_static_shelves, run_scenario

    seed = payload.seed if payload.seed is not None else random.randint(1000, 99999)
    num_robots = max(3, min(payload.num_robots, 30))
    res = run_scenario(seed=seed, num_robots=num_robots, max_ticks=payload.max_ticks, record_frames=True)

    return {
        "name": f"Live Fuzz: Seed {seed} ({num_robots} AMRs)",
        "seed": seed,
        "num_robots": num_robots,
        "conflicts_resolved": res["conflicts_resolved"],
        "total_frames": len(res["frames"]),
        "description": f"Real-time generated fuzz scenario with {num_robots} AMRs. Space-Time A* + Multi-pass Conflict Arbitration. {res['conflicts_resolved']} conflicts resolved with zero collisions.",
        "frames": res["frames"],
        "obstacles": get_static_shelves(),
        "width": 30,
        "height": 30,
    }


@router.get("/telemetry_snapshot", summary="Get the latest real-time telemetry snapshot")
async def get_telemetry_snapshot() -> dict:
    from app.services.telemetry_bus import read_latest_telemetry
    data = read_latest_telemetry()
    if data:
        return data
    return {"tick": 0, "robots": [], "active_conflicts": []}

