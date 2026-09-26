"""
halow_transport.py — Simulated 802.11ah (WiFi HaLow) Transport Channel.

Simulates long-range, low-bandwidth (~150 kbps) telemetry/inventory uplink to the
central dashboard with token-bucket rate limiting and coalesce-to-latest queuing.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import time
from typing import Any, Dict, List, Optional

from app.transport.base import Transport

log = logging.getLogger(__name__)

# Default WiFi HaLow bandwidth budget: 150 kbps = 18,750 bytes/sec
DEFAULT_HALOW_BITRATE_BPS = 150_000
DEFAULT_HALOW_BYTES_PER_SEC = DEFAULT_HALOW_BITRATE_BPS // 8
DEFAULT_BURST_CAPACITY_BYTES = 5_000


class TokenBucket:
    """Token bucket rate limiter for low-bandwidth HaLow channel throttling."""

    def __init__(
        self,
        rate_bytes_per_sec: float = DEFAULT_HALOW_BYTES_PER_SEC,
        capacity_bytes: float = DEFAULT_BURST_CAPACITY_BYTES,
    ) -> None:
        self.rate = rate_bytes_per_sec
        self.capacity = capacity_bytes
        self.tokens = capacity_bytes
        self.last_update = time.monotonic()

    def consume(self, num_bytes: int) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_update
        self.last_update = now

        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= num_bytes:
            self.tokens -= num_bytes
            return True
        return False

    def refill(self, num_bytes: Optional[int] = None) -> None:
        """Refills tokens explicitly (useful for tick-based stepping or testing)."""
        self.last_update = time.monotonic()
        if num_bytes is None:
            self.tokens = self.capacity
        else:
            self.tokens = min(self.capacity, self.tokens + num_bytes)

    def get_utilization(self) -> float:
        """Returns approximate channel utilization ratio [0.0, 1.0]."""
        return max(0.0, min(1.0, 1.0 - (self.tokens / self.capacity)))


class HaLowTransport(Transport):
    """
    Simulated 802.11ah WiFi HaLow transport.
    Sends rate-limited, channel-tagged packets to the central dashboard.
    """

    def __init__(
        self,
        node_id: str,
        dashboard_port: int = 9099,
        host: str = "127.0.0.1",
        bitrate_bps: int = DEFAULT_HALOW_BITRATE_BPS,
        packet_loss_pct: float = 0.0,
    ) -> None:
        self.node_id = node_id
        self.dashboard_port = dashboard_port
        self.host = host
        self.packet_loss_pct = max(0.0, min(100.0, packet_loss_pct))

        bytes_per_sec = bitrate_bps // 8
        self.bucket = TokenBucket(rate_bytes_per_sec=bytes_per_sec)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SO_REUSEADDR"):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
        self.sock.setblocking(False)

        # Metrics
        self.total_bytes_sent = 0
        self.total_packets_sent = 0
        self.dropped_burst_packets = 0
        self.last_sent_timestamp: float = 0.0

        # Outbound queue for burst coalescing
        self._pending_shelf_snapshots: Dict[str, Dict[str, Any]] = {}

    def set_packet_loss(self, pct: float) -> None:
        self.packet_loss_pct = max(0.0, min(100.0, pct))

    def send(self, peer_id: str, payload: Dict[str, Any]) -> None:
        """
        Transmits payload to the DASHBOARD via WiFi HaLow channel.
        Applies token-bucket throttling and coalesces bursts.
        """
        # Tag payload channel as HALOW
        if isinstance(payload, dict):
            if "body" in payload and isinstance(payload["body"], dict) and "payload" in payload["body"]:
                payload["body"]["payload"]["channel"] = "HALOW"
            else:
                payload["channel"] = "HALOW"

        raw = json.dumps(payload).encode("utf-8")
        packet_size = len(raw)

        # Check chaos packet drop
        if self.packet_loss_pct > 0.0 and random.random() * 100.0 < self.packet_loss_pct:
            return

        # Attempt token consumption
        if not self.bucket.consume(packet_size):
            # Burst throttling: coalesce snapshot to latest per shelf_id if present
            shelf_id = None
            if isinstance(payload, dict):
                inner = payload.get("body", {}).get("payload", {}) or payload
                shelf_id = inner.get("shelf_id")

            if shelf_id:
                self._pending_shelf_snapshots[shelf_id] = payload
            self.dropped_burst_packets += 1
            log.debug(f"[HaLow Throttled] Token bucket exhausted on {self.node_id}, packet buffered/coalesced.")
            return

        try:
            self.sock.sendto(raw, (self.host, self.dashboard_port))
            self.total_bytes_sent += packet_size
            self.total_packets_sent += 1
            self.last_sent_timestamp = time.time()
        except Exception as e:
            log.debug(f"[HaLow Send Error] {self.node_id} -> {self.host}:{self.dashboard_port}: {e}")

    @property
    def pending_count(self) -> int:
        return len(self._pending_shelf_snapshots)

    def flush_pending(self) -> int:
        """
        Flushes coalesced latest shelf snapshots if tokens become available.
        Returns number of successfully sent/drained snapshots.
        """
        if not self._pending_shelf_snapshots:
            return 0

        flushed = 0
        for shelf_id, payload in list(self._pending_shelf_snapshots.items()):
            # Chaos packet drop during flush attempt
            if self.packet_loss_pct > 0.0 and random.random() * 100.0 < self.packet_loss_pct:
                continue

            raw = json.dumps(payload).encode("utf-8")
            if self.bucket.consume(len(raw)):
                try:
                    self.sock.sendto(raw, (self.host, self.dashboard_port))
                    self.total_bytes_sent += len(raw)
                    self.total_packets_sent += 1
                    self.last_sent_timestamp = time.time()
                    del self._pending_shelf_snapshots[shelf_id]
                    flushed += 1
                except Exception:
                    del self._pending_shelf_snapshots[shelf_id]
                    flushed += 1
            else:
                break
        return flushed

    def recv_all(self) -> List[Dict[str, Any]]:
        """HaLow uplink is primarily outbound from robot to dashboard in this topology."""
        return []

    def get_status(self) -> Dict[str, Any]:
        return {
            "channel": "HALOW",
            "bitrate_bps": DEFAULT_HALOW_BITRATE_BPS,
            "total_bytes_sent": self.total_bytes_sent,
            "total_packets_sent": self.total_packets_sent,
            "dropped_burst_packets": self.dropped_burst_packets,
            "pending_snapshots": len(self._pending_shelf_snapshots),
            "utilization": round(self.bucket.get_utilization(), 3),
            "last_sent_age_s": round(time.time() - self.last_sent_timestamp, 2) if self.last_sent_timestamp > 0 else None,
        }


    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass
