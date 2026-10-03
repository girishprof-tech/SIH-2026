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
from fastapi import APIRouter, HTTPException, status, Request
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
async def launch_map(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    Validates and launches the specified warehouse map.
    Derives world geometry, obstacles, stations, chutes, gates, and robot configurations.
    Stops current orchestrator, resets state, reseeds inventory, spawns new fleet,
    and broadcasts MAP_LAUNCHED over WebSocket.
    """
    import os
    import time
    from app.models.robot import Robot, AMRType, Heading, RobotState
    from app.services.reservations import clear_all_reservations, register_pod_slots
    from app.services.fleet_orchestrator import FleetOrchestrator

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

        # 1. Cleanly stop current orchestrator processes and free ports
        old_orch = getattr(request.app.state, "orchestrator", None)
        if old_orch is not None:
            logger.info("[MAP LAUNCH] Stopping current orchestrator and freeing UDP ports...")
            try:
                old_orch.stop()
                import time
                time.sleep(0.2)
            except Exception as e:
                logger.warning(f"[MAP LAUNCH] Error stopping old orchestrator: {e}")
            request.app.state.orchestrator = None

        # 2. Reset TaskManager
        task_manager = getattr(request.app.state, "task_manager", None)
        if task_manager is not None:
            task_manager._tasks.clear()

        # 2b. Reset OrderManager
        order_manager = getattr(request.app.state, "order_manager", None)
        if order_manager is not None:
            order_manager.clear()

        # 3. Reset FleetState
        fleet_state = getattr(request.app.state, "fleet_state", None) or get_fleet_state()
        fleet_state.world = new_world
        fleet_state.robots.clear()
        fleet_state.tasks.clear()
        fleet_state.temp_obstacles.clear()
        fleet_state.tick = 0

        # 4. Reset reservations and pod claims
        clear_all_reservations()
        if new_world.pod_slots:
            register_pod_slots(new_world.pod_slots)

        # 5. Rotate/archive job journal and reset recovery state
        journal = getattr(request.app.state, "job_journal", None)
        if journal is not None:
            journal.rotate_session()
        request.app.state.uncompleted_jobs_pending = []

        # 6. Reset telemetry metrics
        telemetry = getattr(request.app.state, "telemetry", None)
        if telemetry is not None:
            telemetry.reset()

        # 7. Reseed inventory ledger from the map's shelf stock
        ledger = getattr(request.app.state, "inventory_ledger", None)
        if ledger is not None:
            ledger.seed_from_map(data, new_world)

        # 8. Start new FleetOrchestrator from map
        spawn_enabled = os.environ.get("SPAWN_FLEET_ORCHESTRATOR", "1") == "1"
        from app.core.config import get_settings
        cfg = get_settings()
        new_orch = FleetOrchestrator(map_data=data, tick_interval_s=cfg.SIM_TICK_MS / 1000.0, max_ticks=0)
        new_orch.reset_logs()

        # Populate fleet_state.robots immediately with initial positions
        for cfg in new_orch.robots_config:
            rid = cfg["robot_id"]
            rtype_str = cfg.get("robot_type", "GOODS_TO_PERSON")
            try:
                rtype_enum = AMRType(rtype_str)
            except Exception:
                rtype_enum = AMRType.GOODS_TO_PERSON
            r = Robot(
                robot_id=rid,
                x=cfg["start"][0],
                y=cfg["start"][1],
                heading=Heading.NORTH,
                state=RobotState.IDLE,
                battery_pct=100.0,
                current_task_id=None,
                priority_score=0,
                last_updated_tick=0,
                robot_type=rtype_enum,
            )
            fleet_state.robots[rid] = r

        if spawn_enabled:
            logger.info(f"[MAP LAUNCH] Spawning new FleetOrchestrator with {len(new_orch.robots_config)} robots...")
            new_orch.start()
            request.app.state.orchestrator = new_orch
        else:
            request.app.state.orchestrator = None

        fleet_state.is_running = True
        request.app.state.telemetry_streaming_paused = False

        # 9. Save to current_map.json for persistent state
        MAPS_DIR.mkdir(parents=True, exist_ok=True)
        with open(CURRENT_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # 10. Broadcast MAP_LAUNCHED over WebSocket
        cm = getattr(request.app.state, "connection_manager", None)
        if cm is not None:
            try:
                await cm.broadcast(json.dumps({
                    "type": "MAP_LAUNCHED",
                    "map_name": data.get("name", "Custom Warehouse"),
                    "width": new_world.width,
                    "height": new_world.height,
                    "robots_count": len(robot_starts),
                    "robots": fleet_state.robots_as_dicts(),
                    "timestamp_ms": int(time.time() * 1000),
                }))
            except Exception as e:
                logger.warning(f"[MAP LAUNCH] Failed to broadcast MAP_LAUNCHED: {e}")

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
    except HTTPException:
        raise
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


class MapSaveRequest(BaseModel):
    name: str
    map_data: Dict[str, Any]


@router.post("/save")
def save_map(payload: MapSaveRequest) -> Dict[str, Any]:
    """Saves a named map specification into the maps directory."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Map name cannot be empty.")
    safe_filename = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name.lower())
    if not safe_filename:
        safe_filename = "custom_map"
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MAPS_DIR / f"{safe_filename}.json"
    map_dict = dict(payload.map_data)
    map_dict["name"] = name
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(map_dict, f, indent=2)
    return {"status": "SAVED", "filename": f"{safe_filename}.json", "path": str(out_path)}


@router.get("/template")
def get_template_map() -> Dict[str, Any]:
    """Returns the built-in warehouse template map."""
    if TEST_MAP_FILE.exists():
        with open(TEST_MAP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return get_current_map()

