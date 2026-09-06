"""
loopback_transport.py — In-Memory Loopback Transport for Deterministic Testing.

Provides deterministic simulation of network phenomena:
  - Packet loss percentage
  - Duplicate message injection
  - Offline/unreachable peers
  - In-memory message queues between nodes without OS networking
"""

from __future__ import annotations

import copy
import random
from typing import Any, Dict, List, Optional, Set

from app.transport.base import Transport


class LoopbackNetworkHub:
    """
    Central simulated network switch that routes messages between LoopbackTransport instances.
    """

    def __init__(self) -> None:
        self.mailboxes: Dict[str, List[Dict[str, Any]]] = {}
        self.offline_nodes: Set[str] = set()

    def register(self, node_id: str) -> None:
        if node_id not in self.mailboxes:
            self.mailboxes[node_id] = []

    def set_offline(self, node_id: str, offline: bool = True) -> None:
        if offline:
            self.offline_nodes.add(node_id)
        else:
            self.offline_nodes.discard(node_id)

    def deliver(self, recipient_id: str, payload: Dict[str, Any]) -> bool:
        if recipient_id in self.offline_nodes:
            return False
        if recipient_id not in self.mailboxes:
            return False
        # Store a deep copy so sender and receiver don't share references
        self.mailboxes[recipient_id].append(copy.deepcopy(payload))
        return True

    def drain(self, node_id: str) -> List[Dict[str, Any]]:
        if node_id in self.offline_nodes or node_id not in self.mailboxes:
            return []
        msgs = self.mailboxes[node_id]
        self.mailboxes[node_id] = []
        return msgs

    def clear(self) -> None:
        self.mailboxes.clear()
        self.offline_nodes.clear()


# Shared default hub for tests
DEFAULT_HUB = LoopbackNetworkHub()


class LoopbackTransport(Transport):
    """
    In-memory transport implementation using LoopbackNetworkHub.
    """

    def __init__(
        self,
        node_id: str,
        hub: Optional[LoopbackNetworkHub] = None,
        packet_loss_pct: float = 0.0,
        duplicate_pct: float = 0.0,
    ) -> None:
        self.node_id = node_id
        self.hub = hub or DEFAULT_HUB
        self.hub.register(node_id)
        self.packet_loss_pct = max(0.0, min(100.0, packet_loss_pct))
        self.duplicate_pct = max(0.0, min(100.0, duplicate_pct))
        self.is_closed = False

    def set_packet_loss(self, pct: float) -> None:
        self.packet_loss_pct = max(0.0, min(100.0, pct))

    def set_duplicate_rate(self, pct: float) -> None:
        self.duplicate_pct = max(0.0, min(100.0, pct))

    def set_offline(self, offline: bool = True) -> None:
        self.hub.set_offline(self.node_id, offline)

    def send(self, peer_id: str, payload: Dict[str, Any]) -> None:
        if self.is_closed:
            return

        # Check packet loss
        if self.packet_loss_pct > 0.0 and random.random() * 100.0 < self.packet_loss_pct:
            return

        self.hub.deliver(peer_id, payload)

        # Check duplicate injection
        if self.duplicate_pct > 0.0 and random.random() * 100.0 < self.duplicate_pct:
            self.hub.deliver(peer_id, payload)

    def recv_all(self) -> List[Dict[str, Any]]:
        if self.is_closed:
            return []
        return self.hub.drain(self.node_id)

    def close(self) -> None:
        self.is_closed = True
