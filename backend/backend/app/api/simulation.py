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
