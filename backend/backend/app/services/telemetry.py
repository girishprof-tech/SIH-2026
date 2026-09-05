"""
Telemetry — metrics collection for GET /api/metrics.

Metrics are updated asynchronously — they never block the simulation tick.
Uses simple counters and exponential moving averages for latency measurements.
"""

from __future__ import annotations

import time
from typing import Dict


class Telemetry:
    """
    Lightweight metrics store.

    All writes are direct attribute assignments — no locks needed in asyncio.
    """

    def __init__(self) -> None:
        self.tick_ms_configured: int = 500
        self.last_tick_processing_ms: float = 0.0
        self.planner_latency_ms: float = 0.0
        self.broadcast_latency_ms: float = 0.0
        self.connected_clients: int = 0
        self.active_robots: int = 0
        self.active_conflicts: int = 0
        self.replans: int = 0
        self.task_injection_latency_ms: float = 0.0
        self.conflict_resolution_latency_ms: float = 0.0
        self.total_ticks: int = 0

        # EMA smoothing factor
        self._alpha: float = 0.3

    def record_tick(self, processing_ms: float) -> None:
        self.last_tick_processing_ms = self._ema(self.last_tick_processing_ms, processing_ms)
        self.total_ticks += 1

    def record_planner(self, latency_ms: float) -> None:
        self.planner_latency_ms = self._ema(self.planner_latency_ms, latency_ms)

    def record_broadcast(self, latency_ms: float) -> None:
        self.broadcast_latency_ms = self._ema(self.broadcast_latency_ms, latency_ms)

    def record_replan(self) -> None:
        self.replans += 1

    def record_task_injection(self, latency_ms: float) -> None:
        self.task_injection_latency_ms = self._ema(self.task_injection_latency_ms, latency_ms)

    def record_conflict_resolution(self, latency_ms: float) -> None:
        self.conflict_resolution_latency_ms = self._ema(
            self.conflict_resolution_latency_ms, latency_ms
        )

    def snapshot(self) -> Dict:
        return {
            "tick_ms_configured": self.tick_ms_configured,
            "last_tick_processing_ms": round(self.last_tick_processing_ms, 3),
            "planner_latency_ms": round(self.planner_latency_ms, 3),
            "broadcast_latency_ms": round(self.broadcast_latency_ms, 3),
            "connected_clients": self.connected_clients,
            "active_robots": self.active_robots,
            "active_conflicts": self.active_conflicts,
            "replans": self.replans,
            "total_ticks": self.total_ticks,
            "task_injection_latency_ms": round(self.task_injection_latency_ms, 3),
            "conflict_resolution_latency_ms": round(self.conflict_resolution_latency_ms, 3),
        }

    def _ema(self, current: float, new: float) -> float:
        """Exponential moving average to smooth noisy latency samples."""
        if current == 0.0:
            return new
        return self._alpha * new + (1 - self._alpha) * current
