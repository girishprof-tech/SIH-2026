"""
WebSocket Connection Manager.

Handles multiple simultaneous clients with:
  - safe connect/disconnect
  - graceful handling of dead clients
  - non-blocking broadcast (slow clients are disconnected, not waited on)
  - single serialization of tick payload for all clients

SCHEMA.md §16: sends TICK_UPDATE every simulation tick.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages all active WebSocket connections.

    Serializes the tick payload ONCE and sends to all connected clients.
    Clients that cannot keep up (queue full or disconnected) are dropped
    without stalling the simulation.
    """

    def __init__(self, max_queue: int = 16) -> None:
        # active sockets — using a set for O(1) add/remove
        self._connections: Set[WebSocket] = set()
        self._max_queue = max_queue

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        log.info("WS_CONNECT clients=%d", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        log.info("WS_DISCONNECT clients=%d", len(self._connections))

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, payload: str) -> None:
        """
        Send the pre-serialized JSON string to all connected clients.

        - Serialization happens ONCE (caller's responsibility).
        - Dead/slow clients are removed without blocking.
        - Uses asyncio.gather for concurrent sends.
        """
        if not self._connections:
            return

        dead: Set[WebSocket] = set()
        tasks = []
        sockets = list(self._connections)  # snapshot to avoid mutation during iteration

        for ws in sockets:
            tasks.append(self._send_safe(ws, payload, dead))

        await asyncio.gather(*tasks, return_exceptions=True)

        for ws in dead:
            self.disconnect(ws)

    async def _send_safe(
        self,
        websocket: WebSocket,
        payload: str,
        dead: Set[WebSocket],
    ) -> None:
        """Send to one client; mark as dead on any error."""
        try:
            await asyncio.wait_for(
                websocket.send_text(payload),
                timeout=0.05,  # 50ms max — never block simulation for a slow client
            )
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, Exception):
            dead.add(websocket)

    async def broadcast_json(self, data: dict) -> None:
        """Convenience: serialize dict then broadcast."""
        import json
        payload = json.dumps(data, separators=(",", ":"))
        await self.broadcast(payload)
