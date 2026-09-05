"""
Reservation model — SCHEMA.md §8.

A reservation is an (x, y, t) tuple.
The reservation table is stored as a dict[(x,y,t)] → robot_id for O(1) lookups.
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

# Type alias for a space-time reservation key
ReservationKey = Tuple[int, int, int]   # (x, y, tick)

# The master reservation table: maps (x, y, t) → robot_id
ReservationTable = Dict[ReservationKey, str]


class ReservationEntry:
    """
    Lightweight snapshot of one robot's reservations.
    Used for releasing when a path is replanned.
    """

    __slots__ = ("robot_id", "keys")

    def __init__(self, robot_id: str, keys: Set[ReservationKey]) -> None:
        self.robot_id = robot_id
        self.keys: Set[ReservationKey] = keys
