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
    fleet = getattr(request.app.state, "fleet_state", None)
    if fleet is not None:
        w = fleet.world
    else:
        from app.models.world import get_active_world
        w = get_active_world()
    return {
        "width": w.width,
        "height": w.height,
        "cell_size_m": w.cell_size_m,
        "static_obstacles": [{"x": x, "y": y} for x, y in sorted(w.static_obstacles)],
        "charging_stations": [{"x": x, "y": y} for x, y in sorted(w.charging_stations)],
        "pickup_stations": [{"x": x, "y": y, "dock_type": "import"} for x, y in sorted(w.pickup_stations)],
        "dropoff_stations": [{"x": x, "y": y, "dock_type": "export"} for x, y in sorted(w.dropoff_stations)],
        "pod_slots": [{"shelf_id": sid, "x": pos[0], "y": pos[1]} for sid, pos in sorted(w.pod_slots.items())],
        "sortation_chutes": w.sortation_chutes,
        "pick_stations": [
            {"id": ps_id, "x": ps["x"], "y": ps["y"], "bufferCount": len(ps.get("buffer", []))}
            for ps_id, ps in w.pick_stations.items()
        ],
        "entry_gates": [
            {"id": gid, "cells": [{"x": c[0], "y": c[1]} for c in cells]}
            for gid, cells in w.import_gates.items()
        ],
        "exit_gates": [
            {"id": gid, "cells": [{"x": c[0], "y": c[1]} for c in cells]}
            for gid, cells in w.export_gates.items()
        ],
    }


@router.get("/api/metrics", summary="Get simulation performance metrics")
async def get_metrics(request: Request) -> dict:
    tel = request.app.state.telemetry
    return tel.snapshot()


@router.get("/api/inventory", summary="Get all warehouse pod shelves and inventory status")
async def get_inventory(request: Request) -> dict:
    from app.services.inventory_ledger import InventoryLedger
    ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()
    fleet = request.app.state.fleet_state
    shelves = ledger.get_all_shelves(current_tick=fleet.tick)
    return {
        "tick": fleet.tick,
        "total_shelves": len(shelves),
        "total_boxes": sum(s.current_box_count for s in shelves),
        "shelves": [s.to_dict() for s in shelves],
    }


@router.get("/api/inventory/{shelf_id}", summary="Get inventory details for a specific shelf")
async def get_shelf_inventory(shelf_id: str, request: Request) -> dict:
    from app.services.inventory_ledger import InventoryLedger
    from fastapi import HTTPException
    ledger = getattr(request.app.state, "inventory_ledger", None) or InventoryLedger()
    fleet = request.app.state.fleet_state
    shelf = ledger.get_shelf(shelf_id, current_tick=fleet.tick)
    if not shelf:
        raise HTTPException(status_code=404, detail=f"Shelf '{shelf_id}' not found")
    return shelf.to_dict()
