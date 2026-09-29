"""
backend/app/api/map_router.py
=============================
Map Management, Validation, Preset Discovery & Dynamic Launch Endpoints for SIH26123.

Endpoints:
- POST /api/map/validate: Validates map schema, reachability, overlaps, and minimums.
- POST /api/map/launch: Validates and hot-loads a custom or preset map, configuring the backend & fleet.
- GET  /api/map/current: Retrieves currently active warehouse map JSON specification.
- GET  /api/map/presets: Discovers all preset maps available in maps/ directory.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.models.world import (
    build_world_from_map_dict,
    get_active_map_data,
    get_active_world,
    set_active_world,
)
from app.services.map_validator import validate_warehouse_map
from app.services.fleet_state import get_fleet_state

logger = logging.getLogger("app.api.map")
router = APIRouter(prefix="/api/map", tags=["map"])

MAPS_DIR = Path(__file__).resolve().parents[4] / "maps"
CURRENT_MAP_FILE = MAPS_DIR / "current_map.json"
TEST_MAP_FILE = MAPS_DIR / "test_map.json"


class MapValidateRequest(BaseModel):
    map_data: Dict[str, Any]


class MapLaunchRequest(BaseModel):
    map_data: Dict[str, Any]
    auto_start_fleet: bool = False


@router.post("/validate")
def validate_map(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validates warehouse map layout against spatial, reachability, and fleet constraints."""
    # Allow either raw map dict or wrapped inside {"map_data": ...}
    data = payload.get("map_data", payload)
    res = validate_warehouse_map(data)
    return res


@router.post("/launch")
def launch_map(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates and launches the specified warehouse map.
    Derives world geometry, obstacles, stations, chutes, gates, and robot configurations.
    """
    if "filename" in payload or "preset_name" in payload:
        fname = payload.get("filename") or payload.get("preset_name")
        target_file = MAPS_DIR / fname
        if not target_file.exists():
            target_file = MAPS_DIR / f"{fname}.json"
        if not target_file.exists():
            raise HTTPException(status_code=404, detail=f"Preset map '{fname}' not found.")
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = payload.get("map_data", payload)

    validation = validate_warehouse_map(data)
    if not validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Map validation failed.", "errors": validation["errors"], "warnings": validation["warnings"]},
        )

    try:
        new_world, robot_starts = build_world_from_map_dict(data)
        set_active_world(new_world, data)

        # Update FleetState singleton
        fleet_state = get_fleet_state()
        fleet_state.world = new_world

        # Save to current_map.json for persistent state
        MAPS_DIR.mkdir(parents=True, exist_ok=True)
        with open(CURRENT_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Re-register pod slots in reservation service
        from app.services.reservations import register_pod_slots
        if new_world.pod_slots:
            register_pod_slots(new_world.pod_slots)

        logger.info(
            f"Successfully launched map '{data.get('name', 'Custom')}' ({new_world.width}x{new_world.height}) "
            f"with {len(robot_starts)} robots, {len(new_world.pod_slots)} shelves, {len(new_world.sortation_chutes)} chutes."
        )

        return {
            "status": "LAUNCHED",
            "name": data.get("name", "Custom Warehouse"),
            "width": new_world.width,
            "height": new_world.height,
            "cell_size_m": new_world.cell_size_m,
            "robots_count": len(robot_starts),
            "shelves_count": len(new_world.pod_slots),
            "chargers_count": len(new_world.charging_stations),
            "chutes_count": len(new_world.sortation_chutes),
            "warnings": validation["warnings"],
        }
    except Exception as exc:
        logger.error(f"Error launching map: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to launch map: {exc}")


@router.get("/current")
def get_current_map() -> Dict[str, Any]:
    """Returns the currently active warehouse map JSON specification."""
    active_data = get_active_map_data()
    if active_data:
        return active_data

    # Check current_map.json file
    if CURRENT_MAP_FILE.exists():
        try:
            with open(CURRENT_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Fallback to test_map.json
    if TEST_MAP_FILE.exists():
        try:
            with open(TEST_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Generate from active world
    world = get_active_world()
    return {
        "schema_version": "1.0.0",
        "name": "Standard Warehouse",
        "grid": {"width": world.width, "height": world.height, "cell_size_m": world.cell_size_m},
        "blocked_cells": [{"x": p[0], "y": p[1]} for p in sorted(world.static_obstacles)],
        "shelves": [{"id": k, "x": v[0], "y": v[1], "capacity": 4} for k, v in sorted(world.pod_slots.items())],
        "chargers": [{"id": f"CHG-{i+1:02d}", "x": c[0], "y": c[1]} for i, c in enumerate(sorted(world.charging_stations))],
        "entry_gates": [{"id": k, "name": f"Gate {k}", "cells": [{"x": p[0], "y": p[1]} for p in v]} for k, v in sorted(world.import_gates.items())],
        "exit_gates": [{"id": k, "name": f"Gate {k}", "cells": [{"x": p[0], "y": p[1]} for p in v]} for k, v in sorted(world.export_gates.items())],
        "sorting_stations": [v for k, v in sorted(world.sortation_chutes.items())],
        "pick_stations": [v for k, v in sorted(world.pick_stations.items())],
        "robot_starts": [],
    }


@router.get("/presets")
def list_map_presets() -> List[Dict[str, Any]]:
    """Returns list of available warehouse map presets."""
    presets = []
    if not MAPS_DIR.exists():
        return presets

    for map_file in MAPS_DIR.glob("*.json"):
        if map_file.name == "current_map.json":
            continue
        try:
            with open(map_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                grid_info = data.get("grid", {})
                presets.append({
                    "filename": map_file.name,
                    "id": map_file.stem,
                    "name": data.get("name", map_file.stem),
                    "description": data.get("description", ""),
                    "width": grid_info.get("width", 30),
                    "height": grid_info.get("height", 30),
                    "shelves_count": len(data.get("shelves", [])),
                    "robots_count": len(data.get("robot_starts", [])),
                    "chutes_count": len(data.get("sorting_stations", [])),
                })
        except Exception:
            continue

    return presets
