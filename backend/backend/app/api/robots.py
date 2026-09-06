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
        "robot_type": robot.robot_type.value,
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


@router.post("/{robot_id}/emergency_stop", summary="Emergency stop target robot")
async def emergency_stop_robot(robot_id: str, request: Request) -> dict:
    fleet = request.app.state.fleet_state
    if robot_id not in fleet.robots:
        raise HTTPException(404, f"Robot {robot_id!r} not found")

    from app.services.task_manager import get_fleet_peer_ports
    from app.security.hmac_envelope import sign_payload
    import socket
    import json
    import time

    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))
    port = peer_ports.get(robot_id)
    sent_udp = False
    if port:
        msg = {
            "type": "EMERGENCY_STOP",
            "robot_id": robot_id,
            "sender_id": "CENTRAL_DISPATCH",
            "timestamp": time.time(),
        }
        envelope = sign_payload(msg, secret_key="sih2026-edge-robot-shared-secret")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(json.dumps(envelope).encode("utf-8"), ("127.0.0.1", port))
            sent_udp = True
        finally:
            sock.close()

    rob = fleet.robots[robot_id]
    try:
        from app.models.robot_fsm import RobotState
        rob.state = RobotState.EMERGENCY_STOP
    except Exception:
        pass

    return {"status": "ok", "message": f"EMERGENCY_STOP dispatched to {robot_id}", "udp_delivered": sent_udp}


@router.post("/{robot_id}/reset", summary="Reset robot from failsafe/emergency stop")
async def reset_robot(robot_id: str, request: Request) -> dict:
    fleet = request.app.state.fleet_state
    if robot_id not in fleet.robots:
        raise HTTPException(404, f"Robot {robot_id!r} not found")

    from app.services.task_manager import get_fleet_peer_ports
    from app.security.hmac_envelope import sign_payload
    import socket
    import json
    import time

    peer_ports = get_fleet_peer_ports(getattr(request.app.state, "orchestrator", None))
    port = peer_ports.get(robot_id)
    sent_udp = False
    if port:
        msg = {
            "type": "RESET",
            "robot_id": robot_id,
            "sender_id": "CENTRAL_DISPATCH",
            "timestamp": time.time(),
        }
        envelope = sign_payload(msg, secret_key="sih2026-edge-robot-shared-secret")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(json.dumps(envelope).encode("utf-8"), ("127.0.0.1", port))
            sent_udp = True
        finally:
            sock.close()

    rob = fleet.robots[robot_id]
    try:
        from app.models.robot_fsm import RobotState
        rob.state = RobotState.IDLE
    except Exception:
        pass

    return {"status": "ok", "message": f"RESET dispatched to {robot_id}", "udp_delivered": sent_udp}
