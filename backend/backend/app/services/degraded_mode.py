"""
degraded_mode.py — Degraded-Network Detector and Speed Limiter for AMRs.

Monitors incoming peer message rate. If message rate drops below threshold,
activates degraded mode:
  - Reduces robot movement speed by 50% (moves on alternate ticks).
  - Shortens reservation expiration window to prevent stale blocking.
"""

from __future__ import annotations

import time
from typing import Dict, List


class DegradedModeDetector:
    """
    Detects degraded network communication based on peer heartbeat/message intervals.
    """

    def __init__(
        self,
        threshold_missing_ticks: int = 3,
        stale_reservation_window_ticks: int = 2,
    ) -> None:
        self.threshold_missing_ticks = threshold_missing_ticks
        self.stale_reservation_window_ticks = stale_reservation_window_ticks
        self.peer_last_ticks: Dict[str, int] = {}
        self.forced_degraded: bool = False

    def record_peer_tick(self, peer_id: str, tick: int) -> None:
        """Records the latest tick observed from a peer."""
        self.peer_last_ticks[peer_id] = max(self.peer_last_ticks.get(peer_id, -1), tick)

    def is_degraded(self, current_tick: int) -> bool:
        """
        Returns True if forced degraded, or any known peer is lagging behind by threshold ticks.
        """
        if self.forced_degraded:
            return True

        if not self.peer_last_ticks:
            return False

        # If any peer hasn't been heard from in threshold ticks, communication is degraded
        for peer_id, last_t in self.peer_last_ticks.items():
            if (current_tick - last_t) >= self.threshold_missing_ticks:
                return True

        return False

    def should_move_this_tick(self, current_tick: int) -> bool:
        """
        In degraded mode, reduces speed by 50% (moves only on even ticks).
        In normal mode, always allows movement every tick.
        """
        if self.is_degraded(current_tick):
            return (current_tick % 2) == 0
        return True

    def set_forced_degraded(self, enabled: bool) -> None:
        self.forced_degraded = enabled
