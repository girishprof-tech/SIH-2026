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
        self._clients_needing_baseline: Set[WebSocket] = set()
        self.latest_baseline_json: Optional[str] = None
        self._max_queue = max_queue

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        sent_baseline = False
        if self.latest_baseline_json:
            try:
                await websocket.send_text(self.latest_baseline_json)
                sent_baseline = True
            except Exception:
                self._clients_needing_baseline.add(websocket)
        else:
            self._clients_needing_baseline.add(websocket)
        log.info("WS_CONNECT clients=%d (baseline_sent=%s)", len(self._connections), sent_baseline)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        self._clients_needing_baseline.discard(websocket)
        log.info("WS_DISCONNECT clients=%d", len(self._connections))

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def broadcast_telemetry(self, full_json: str, delta_json: str) -> None:
        """
        Broadcasts telemetry:
        - Sends full baseline state to newly connected or reconnecting clients.
        - Sends compact delta state to established clients.
        """
        self.latest_baseline_json = full_json
        if not self._connections:
            return

        dead: Set[WebSocket] = set()
        tasks = []

        # 1. Any client that connected before full_json was cached gets full baseline
        needing_baseline = list(self._clients_needing_baseline)
        self._clients_needing_baseline.clear()
        for ws in needing_baseline:
            if ws in self._connections:
                tasks.append(self._send_safe(ws, full_json, dead))

        # 2. Established clients get compact delta
        established = [ws for ws in list(self._connections) if ws not in needing_baseline]
        for ws in established:
            tasks.append(self._send_safe(ws, delta_json, dead))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for ws in dead:
            self.disconnect(ws)

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

    async def broadcast_delta(self, payload: str) -> None:
        """Broadcast a delta update to all active WebSocket clients."""
        await self.broadcast(payload)


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
                timeout=1.0,  # 1.0s max — safe margin without stalling simulation
            )
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, Exception):
            dead.add(websocket)

    async def broadcast_json(self, data: dict) -> None:
        """Convenience: serialize dict then broadcast."""
        import json
        payload = json.dumps(data, separators=(",", ":"))
        await self.broadcast(payload)
