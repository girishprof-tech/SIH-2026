"""
base.py — Transport Abstraction Interface for Autonomous AMRs.

Defines the pluggable communication contract for peer-to-peer messaging
and centralized telemetry/task reporting.

Swappability:
Any networking protocol (e.g. non-blocking UDP sockets, loopback in-memory queues,
MQTT, ZeroMQ, or ROS2 DDS) can be swapped in seamlessly by subclassing Transport
and implementing the four abstract methods below without modifying robot logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class Transport(ABC):
    """Abstract communication transport interface for robot nodes."""

    @abstractmethod
    def send(self, peer_id: str, payload: Dict[str, Any]) -> None:
        """
        Sends a JSON-serializable message payload to a specific peer robot or coordinator.
        Must be non-blocking.
        """
        pass

    @abstractmethod
    def recv_all(self) -> List[Dict[str, Any]]:
        """
        Receives and drains all pending incoming messages without blocking.
        Returns a list of decoded message dictionaries.
        """
        pass

    @abstractmethod
    def set_packet_loss(self, pct: float) -> None:
        """
        Configures simulated packet drop rate as a percentage [0.0, 100.0].
        """
        pass

    @abstractmethod
    def close(self) -> None:
        """
        Cleanly closes underlying transport resources (sockets, queues, connections).
        """
        pass
