"""
WebSocket endpoint — SCHEMA.md §16.

GET /ws/fleet

Sends TICK_UPDATE messages every simulation tick.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/fleet")
async def websocket_fleet(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for the fleet dashboard.

    Connect to receive TICK_UPDATE messages every simulation tick.
    SCHEMA.md §16 payload format:
    {
      "type": "TICK_UPDATE",
      "tick": int,
      "timestamp_ms": int,
      "robots": [...],
      "active_conflicts": [...],
      "temporary_obstacles": [...]
    }
    """
    manager = websocket.app.state.connection_manager

    await manager.connect(websocket)
    telemetry = websocket.app.state.telemetry

    try:
        # Keep the connection alive; broadcast is driven by the simulation loop
        while True:
            # We only need to receive to detect client disconnect
            data = await websocket.receive_text()
            # Clients may send control messages (e.g., ping) — we silently ignore them
            log.debug("WS received (ignored): %s", data[:64])
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("WS error: %s", exc)
    finally:
        manager.disconnect(websocket)
        telemetry.connected_clients = manager.client_count
