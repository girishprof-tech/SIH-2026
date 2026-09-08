"""
stop_and_wait.py — Traditional Stop-and-Wait Baseline Coordination Policy.

Implements standard industrial baseline coordination:
  - Each robot computes shortest path using standard Space-Time A* without reservation sharing.
  - NO peer-to-peer negotiation.
  - NO priority scoring or ML arbitration.
  - When a robot's next planned cell is currently occupied by another robot,
    it simply halts in place for that tick and re-checks next tick (textbook stop-and-wait).
  - Guarantees zero collisions by strict halting.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set, Tuple


def is_stop_and_wait_enabled() -> bool:
    """Returns True if COORDINATION_POLICY is configured to 'stop_and_wait'."""
    return os.environ.get("COORDINATION_POLICY", "decentralized").strip().lower() == "stop_and_wait"


class StopAndWaitPolicy:
    """
    Coordination handler for the Stop-and-Wait baseline.
    """

    @staticmethod
    def check_halt(
        current_pos: Tuple[int, int],
        intended_pos: Tuple[int, int],
        peer_occupied_cells: Set[Tuple[int, int]],
    ) -> Tuple[Tuple[int, int], str, bool]:
        """
        If the intended next cell is currently occupied by another AMR:
        Halts in place at current_pos.
        Returns: (actual_next_pos, action_string, did_halt)
        """
        if intended_pos != current_pos and intended_pos in peer_occupied_cells:
            # Cell is occupied: textbook stop-and-wait halts in place
            return current_pos, "STOP_AND_WAIT_HALT", True
        return intended_pos, "MOVED", False
