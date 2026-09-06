"""
udp_transport.py — Real UDP Transport Implementation for Autonomous AMRs.

Uses standard non-blocking UDP sockets over IP (default 127.0.0.1 for local execution,
configurable to real LAN addresses for multi-machine deployment).
"""

from __future__ import annotations

import json
import logging
import random
import socket
from typing import Any, Dict, List, Optional

from app.transport.base import Transport

log = logging.getLogger(__name__)


class UdpTransport(Transport):
    """
    Real UDP socket transport with port routing and optional chaos packet loss.
    """

    def __init__(
        self,
        node_id: str,
        port: int,
        peer_ports: Dict[str, int],
        host: str = "127.0.0.1",
        packet_loss_pct: float = 0.0,
    ) -> None:
        self.node_id = node_id
        self.port = port
        self.peer_ports = dict(peer_ports)
        self.host = host
        self.packet_loss_pct = max(0.0, min(100.0, packet_loss_pct))

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except Exception:
                pass
        elif hasattr(socket, "SO_REUSEADDR"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.setblocking(False)

    def set_packet_loss(self, pct: float) -> None:
        """Sets packet loss percentage [0.0, 100.0]."""
        self.packet_loss_pct = max(0.0, min(100.0, pct))

    def send(self, peer_id: str, payload: Dict[str, Any]) -> None:
        """Sends payload to peer UDP port if peer exists and not dropped by chaos."""
        if peer_id not in self.peer_ports:
            return

        if self.packet_loss_pct > 0.0 and random.random() * 100.0 < self.packet_loss_pct:
            # Simulated chaos drop
            return

        target_port = self.peer_ports[peer_id]
        try:
            raw = json.dumps(payload).encode("utf-8")
            self.sock.sendto(raw, (self.host, target_port))
        except Exception as e:
            log.debug("UDP send error to %s (%s:%d): %s", peer_id, self.host, target_port, e)

    def recv_all(self) -> List[Dict[str, Any]]:
        """Drains all available incoming UDP datagrams without blocking."""
        messages: List[Dict[str, Any]] = []
        while True:
            try:
                raw_data, _ = self.sock.recvfrom(4096)
            except (BlockingIOError, socket.error):
                break
            except Exception:
                break

            if self.packet_loss_pct > 0.0 and random.random() * 100.0 < self.packet_loss_pct:
                # Simulated chaos receive drop
                continue

            try:
                msg = json.loads(raw_data.decode("utf-8"))
                if isinstance(msg, dict):
                    messages.append(msg)
            except Exception:
                continue

        return messages

    def close(self) -> None:
        """Closes the socket."""
        try:
            self.sock.close()
        except Exception:
            pass
