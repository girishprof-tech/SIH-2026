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

from typing import Any, Dict, List, Optional, Tuple

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


# ─────────────────────────────────────────────────────────────────────────────
# Pod-Slot & Shelf Occupancy Reservations (Phase 1.5 Fix 1)
# ─────────────────────────────────────────────────────────────────────────────

import threading

_pod_lock = threading.Lock()

# Global shared pod claims: shelf_id -> {"robot_id": str, "claimed_tick": int, "expires_tick": int}
SHARED_POD_CLAIMS: Dict[str, Dict[str, Any]] = {}
SHARED_POD_SLOTS: Dict[str, Tuple[int, int]] = {}
DEFAULT_POD_LEASE_TICKS = 40


def register_pod_slots(slots: Dict[str, Tuple[int, int]]) -> None:
    """Registers known pod slot coordinates {shelf_id: (x, y)}."""
    with _pod_lock:
        SHARED_POD_SLOTS.update(slots)


def claim_pod(
    shelf_id: str,
    robot_id: str,
    current_tick: int = 0,
    lease_ticks: int = DEFAULT_POD_LEASE_TICKS,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    """
    Atomically claims a shelf_id for robot_id with a bounded lease (TTL).
    Returns True if successfully claimed or renewed by robot_id, False if claimed by another robot.
    """
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        claim = target.get(shelf_id)
        if claim is not None:
            # Check if active by another robot
            if claim["robot_id"] != robot_id and claim["expires_tick"] > current_tick:
                return False
        # Free or expired or owned by robot_id -> grant claim
        target[shelf_id] = {
            "robot_id": robot_id,
            "claimed_tick": current_tick,
            "expires_tick": current_tick + lease_ticks,
        }
        return True


def renew_pod_claim(
    shelf_id: str,
    robot_id: str,
    current_tick: int,
    lease_ticks: int = DEFAULT_POD_LEASE_TICKS,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    """Extends the lease of an existing claim owned by robot_id."""
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        claim = target.get(shelf_id)
        if claim and claim["robot_id"] == robot_id:
            claim["expires_tick"] = current_tick + lease_ticks
            return True
        return False


def release_pod(
    shelf_id: str,
    robot_id: str,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> bool:
    """Releases a shelf_id claim if currently held by robot_id."""
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        claim = target.get(shelf_id)
        if claim and claim["robot_id"] == robot_id:
            del target[shelf_id]
            return True
        return False


def release_robot_pod_claims(
    robot_id: str,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Releases all pod claims owned by robot_id (e.g. on robot crash/disconnect/estop)."""
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        released = [sid for sid, c in target.items() if c["robot_id"] == robot_id]
        for sid in released:
            del target[sid]
        return released


def get_pod_claim(
    shelf_id: str,
    current_tick: Optional[int] = None,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[str]:
    """Returns the robot_id holding an active claim on shelf_id, or None if free/expired."""
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        claim = target.get(shelf_id)
        if claim is None:
            return None
        if current_tick is not None and claim["expires_tick"] <= current_tick:
            return None
        return claim["robot_id"]


def prune_stale_pod_claims(
    current_tick: int,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Prunes expired pod claims whose lease has elapsed. Returns list of released shelf_ids."""
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        stale = [sid for sid, c in target.items() if c["expires_tick"] <= current_tick]
        for sid in stale:
            del target[sid]
        return stale


def record_peer_pod_claim(
    shelf_id: str,
    robot_id: str,
    current_tick: int,
    lease_ticks: int = DEFAULT_POD_LEASE_TICKS,
    pod_claims: Optional[Dict[str, Dict[str, Any]]] = None,
) -> None:
    """Updates local pod claims cache with an authenticated claim from a peer AMR."""
    target = SHARED_POD_CLAIMS if pod_claims is None else pod_claims
    with _pod_lock:
        target[str(shelf_id)] = {
            "robot_id": str(robot_id),
            "claimed_tick": current_tick,
            "expires_tick": current_tick + lease_ticks,
        }


def reserve_pod_slot(
    slot_pos: Tuple[int, int],
    robot_id: str,
    start_tick: int,
    duration_ticks: int,
    reservation_table: ReservationTable,
) -> None:
    """Reserves the pod slot coordinates (x, y) across [start_tick, start_tick + duration_ticks]."""
    for t in range(start_tick, start_tick + duration_ticks + 1):
        reservation_table[(slot_pos[0], slot_pos[1], t)] = robot_id


# ─────────────────────────────────────────────────────────────────────────────
# Charging Station Reservations & Claims (Part C)
# ─────────────────────────────────────────────────────────────────────────────

_charger_lock = threading.Lock()
SHARED_CHARGER_CLAIMS: Dict[Tuple[int, int], Dict[str, Any]] = {}
DEFAULT_CHARGER_LEASE_TICKS = 40


def claim_charger(
    station_pos: Tuple[int, int],
    robot_id: str,
    current_tick: int = 0,
    lease_ticks: int = DEFAULT_CHARGER_LEASE_TICKS,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> bool:
    """
    Part C: Atomically claims a charging station (x, y) for robot_id with a bounded lease (TTL).
    Returns True if successfully claimed or renewed by robot_id, False if claimed by another robot.
    """
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    with _charger_lock:
        claim = target.get(station_pos)
        if claim is not None:
            if claim["robot_id"] != robot_id and claim["expires_tick"] > current_tick:
                return False
        target[station_pos] = {
            "robot_id": robot_id,
            "claimed_tick": current_tick,
            "expires_tick": current_tick + lease_ticks,
        }
        return True


def renew_charger_claim(
    station_pos: Tuple[int, int],
    robot_id: str,
    current_tick: int,
    lease_ticks: int = DEFAULT_CHARGER_LEASE_TICKS,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> bool:
    """Extends the lease of an existing charger claim owned by robot_id."""
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    with _charger_lock:
        claim = target.get(station_pos)
        if claim and claim["robot_id"] == robot_id:
            claim["expires_tick"] = current_tick + lease_ticks
            return True
        return False


def release_charger(
    station_pos: Tuple[int, int],
    robot_id: str,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> bool:
    """Releases a charging station claim if currently held by robot_id."""
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    with _charger_lock:
        claim = target.get(station_pos)
        if claim and claim["robot_id"] == robot_id:
            del target[station_pos]
            return True
        return False


def release_robot_charger_claims(
    robot_id: str,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> List[Tuple[int, int]]:
    """Releases all charging station claims owned by robot_id."""
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    with _charger_lock:
        released = [pos for pos, c in target.items() if c["robot_id"] == robot_id]
        for pos in released:
            del target[pos]
        return released


def get_charger_claim(
    station_pos: Tuple[int, int],
    current_tick: Optional[int] = None,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> Optional[str]:
    """Returns the robot_id holding an active claim on station_pos, or None if free/expired."""
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    with _charger_lock:
        claim = target.get(station_pos)
        if claim is None:
            return None
        if current_tick is not None and claim["expires_tick"] <= current_tick:
            return None
        return claim["robot_id"]


def prune_stale_charger_claims(
    current_tick: int,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> List[Tuple[int, int]]:
    """Prunes expired charger claims whose lease has elapsed. Returns list of released station positions."""
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    with _charger_lock:
        stale = [pos for pos, c in target.items() if c["expires_tick"] <= current_tick]
        for pos in stale:
            del target[pos]
        return stale


def record_peer_charger_claim(
    station_pos: Tuple[int, int],
    robot_id: str,
    current_tick: int,
    lease_ticks: int = DEFAULT_CHARGER_LEASE_TICKS,
    charger_claims: Optional[Dict[Tuple[int, int], Dict[str, Any]]] = None,
) -> None:
    """Updates local charger claims cache with an authenticated claim from a peer AMR."""
    target = SHARED_CHARGER_CLAIMS if charger_claims is None else charger_claims
    pos = (int(station_pos[0]), int(station_pos[1]))
    with _charger_lock:
        target[pos] = {
            "robot_id": str(robot_id),
            "claimed_tick": current_tick,
            "expires_tick": current_tick + lease_ticks,
        }


def clear_all_claims() -> None:
    """Clears all shared pod and charger claims (used for test isolation / reset)."""
    with _pod_lock:
        SHARED_POD_CLAIMS.clear()
        SHARED_POD_SLOTS.clear()
    with _charger_lock:
        SHARED_CHARGER_CLAIMS.clear()


