"""
reservations.py — helpers around the shared reservation table.
Owner: Member 2 — Core Algorithm Engineer.  SIH26123.

SCHEMA.md Section 1 ("Reservation window") says a robot reserves its ENTIRE
computed path, start to goal, and that reservations are released/recomputed
whenever a path changes. This module is the small bit of bookkeeping that
implements that rule so Member 3 (conflict engine) and Member 4 (backend
broker) don't each reinvent it slightly differently.

The reservation table itself is just a plain dict as specified in the
contract:

    reservation_table: dict[tuple[int, int, int], str]   # (x, y, t) -> robot_id

Typical per-robot replan cycle:

    release_reservations(robot_id, reservation_table)      # drop stale claim
    new_path = find_path(start, goal, tick, reservation_table, robot_id=robot_id)
    reserve_path(new_path, robot_id, reservation_table)     # claim the new one
"""

from __future__ import annotations

from typing import Dict, List, Tuple

ReservationTable = Dict[Tuple[int, int, int], str]


def reserve_path(
    path: List[dict],
    robot_id: str,
    reservation_table: ReservationTable,
    hold_ticks_at_goal: int = 0,
) -> None:
    """
    Write every (x, y, t) step of `path` into reservation_table as owned by
    `robot_id`. Per SCHEMA.md Section 1, the whole path is reserved up front,
    not just the next few ticks.

    `hold_ticks_at_goal` optionally extends the reservation at the final cell
    for extra ticks (useful for a robot that will sit there loading/unloading
    before its next task is assigned) — this is not required by the SCHEMA
    but is a common, safe extension. It never overwrites another robot's
    existing claim.
    """
    for step in path:
        reservation_table[(step["x"], step["y"], step["t"])] = robot_id

    if hold_ticks_at_goal and path:
        last = path[-1]
        for extra in range(1, hold_ticks_at_goal + 1):
            key = (last["x"], last["y"], last["t"] + extra)
            reservation_table.setdefault(key, robot_id)


def release_reservations(robot_id: str, reservation_table: ReservationTable) -> None:
    """
    Remove every entry owned by `robot_id`. Call this before replanning a
    robot's path so its own previous (now-stale) reservation can't block the
    new search.
    """
    stale = [key for key, owner in reservation_table.items() if owner == robot_id]
    for key in stale:
        del reservation_table[key]


def prune_past(reservation_table: ReservationTable, current_tick: int) -> int:
    """
    Drop every reservation whose tick has already elapsed, so the table
    doesn't grow without bound over a long-running simulation. Returns the
    number of entries removed. Safe to call once per tick from Member 4's
    simulation loop.
    """
    stale = [key for key in reservation_table if key[2] < current_tick]
    for key in stale:
        del reservation_table[key]
    return len(stale)
