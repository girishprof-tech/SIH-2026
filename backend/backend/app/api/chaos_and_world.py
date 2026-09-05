"""
Obstacle REST endpoints.

POST   /api/obstacles           — add temporary obstacle
DELETE /api/obstacles/{id}      — remove temporary obstacle
GET    /api/obstacles           — list active obstacles
GET    /api/world               — get full warehouse config
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request

from app.models.obstacle import TemporaryObstacle
from app.schemas.obstacle import ObstacleCreateRequest, ObstacleOut

log = logging.getLogger(__name__)
router = APIRouter(tags=["World & Obstacles"])


@router.post(
    "/api/obstacles",
    summary="Add a temporary obstacle",
    description="Injects a time-limited obstacle into the simulation. Robots in its path will replan.",
    response_model=ObstacleOut,
    status_code=201,
)
async def add_obstacle(body: ObstacleCreateRequest, request: Request) -> ObstacleOut:
    fleet = request.app.state.fleet_state

    if not fleet.world.in_bounds(body.x, body.y):
        raise HTTPException(400, f"Position ({body.x},{body.y}) is out of bounds")
    if fleet.world.is_static_blocked(body.x, body.y):
        raise HTTPException(400, f"Position ({body.x},{body.y}) is already a static obstacle")
    if body.obstacle_id in fleet.temp_obstacles:
        raise HTTPException(409, f"Obstacle {body.obstacle_id!r} already exists")

    obs = TemporaryObstacle(
        obstacle_id=body.obstacle_id,
        x=body.x,
        y=body.y,
        created_tick=fleet.tick,
        expires_at_tick=fleet.tick + body.duration_ticks,
    )
    fleet.add_temp_obstacle(obs)
    log.info("OBSTACLE_QUEUED id=%s pos=(%d,%d) duration=%d", body.obstacle_id, body.x, body.y, body.duration_ticks)

    return ObstacleOut(
        obstacle_id=obs.obstacle_id,
        position={"x": obs.x, "y": obs.y},
        created_tick=obs.created_tick,
        expires_at_tick=obs.expires_at_tick,
    )


@router.delete(
    "/api/obstacles/{obstacle_id}",
    summary="Remove a temporary obstacle early",
    status_code=200,
)
async def remove_obstacle(obstacle_id: str, request: Request) -> dict:
    fleet = request.app.state.fleet_state
    if obstacle_id not in fleet.temp_obstacles:
        # Also check pending queue (edge case: added this tick, not flushed yet)
        pending_ids = {o.obstacle_id for o in fleet._pending_obstacles}
        if obstacle_id not in pending_ids:
            raise HTTPException(404, f"Obstacle {obstacle_id!r} not found")
    fleet.remove_temp_obstacle(obstacle_id)
    return {"status": "removed", "obstacle_id": obstacle_id}


@router.get("/api/obstacles", summary="List active temporary obstacles", response_model=List[ObstacleOut])
async def list_obstacles(request: Request) -> List[ObstacleOut]:
    fleet = request.app.state.fleet_state
    return [
        ObstacleOut(
            obstacle_id=obs.obstacle_id,
            position={"x": obs.x, "y": obs.y},
            created_tick=obs.created_tick,
            expires_at_tick=obs.expires_at_tick,
        )
        for obs in fleet.temp_obstacles.values()
        if obs.is_active(fleet.tick)
    ]


@router.get("/api/world", summary="Get warehouse world configuration")
async def get_world(request: Request) -> dict:
    fleet = request.app.state.fleet_state
    w = fleet.world
    return {
        "width": w.width,
        "height": w.height,
        "cell_size_m": w.cell_size_m,
        "static_obstacles": [{"x": x, "y": y} for x, y in sorted(w.static_obstacles)],
        "charging_stations": [{"x": x, "y": y} for x, y in sorted(w.charging_stations)],
        "pickup_stations": [{"x": x, "y": y} for x, y in sorted(w.pickup_stations)],
        "dropoff_stations": [{"x": x, "y": y} for x, y in sorted(w.dropoff_stations)],
    }


@router.get("/api/metrics", summary="Get simulation performance metrics")
async def get_metrics(request: Request) -> dict:
    tel = request.app.state.telemetry
    return tel.snapshot()
