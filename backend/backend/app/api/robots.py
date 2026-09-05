"""
Robot REST endpoints.

GET /api/robots         — list all robots
GET /api/robots/{id}    — get single robot state
"""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/robots", tags=["Robots"])


@router.get("/", summary="List all robot states")
async def list_robots(request: Request) -> List[dict]:
    fleet = request.app.state.fleet_state
    return fleet.robots_as_dicts()


@router.get("/{robot_id}", summary="Get single robot state")
async def get_robot(robot_id: str, request: Request) -> dict:
    fleet = request.app.state.fleet_state
    robot = fleet.robots.get(robot_id)
    if not robot:
        raise HTTPException(404, f"Robot {robot_id!r} not found")
    return {
        "robot_id": robot.robot_id,
        "position": {"x": robot.x, "y": robot.y},
        "heading": robot.heading.value,
        "state": robot.state.value,
        "battery_pct": round(robot.battery_pct, 2),
        "current_task_id": robot.current_task_id,
        "priority_score": robot.priority_score,
        "last_updated_tick": robot.last_updated_tick,
        "path": [{"x": n.x, "y": n.y, "t": n.t} for n in robot.path],
        "_internal": {
            "wait_ticks": robot._wait_ticks,
            "replan_count": robot._replan_count,
            "needs_replan": robot._needs_replan,
        },
    }
