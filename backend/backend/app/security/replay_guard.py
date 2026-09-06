"""
replay_guard.py — Monotonic Sequence and Freshness Window Replay Protection.

Guards against message replay attacks and expired network packets.
"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple


class ReplayGuard:
    """
    Tracks highest received sequence number per sender and enforces a timestamp freshness window.
    """

    def __init__(self, freshness_window_s: float = 5.0) -> None:
        self.freshness_window_s = freshness_window_s
        self.last_seq: Dict[str, int] = {}

    def validate(
        self,
        sender_id: str,
        seq: Optional[int],
        timestamp: float,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Validates timestamp freshness and monotonic sequence numbers.
        Returns: (is_valid, reason)
        """
        now = current_time if current_time is not None else time.time()

        # 1. Check timestamp freshness window
        age = now - timestamp
        if age > self.freshness_window_s:
            return False, f"Expired message timestamp: age {age:.2f}s > window {self.freshness_window_s:.2f}s"
        if age < -2.0:  # Allow max 2s future clock skew
            return False, f"Message from future timestamp (clock skew): {age:.2f}s"

        # 2. Check monotonic sequence if provided
        if seq is not None:
            prev_seq = self.last_seq.get(sender_id, -1)
            if seq <= prev_seq:
                return False, f"Replay detected: seq {seq} <= last seen {prev_seq} for sender {sender_id}"
            self.last_seq[sender_id] = seq

        return True, ""

    def reset_sender(self, sender_id: str) -> None:
        if sender_id in self.last_seq:
            del self.last_seq[sender_id]

    def clear(self) -> None:
        self.last_seq.clear()
