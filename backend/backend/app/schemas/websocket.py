"""
Pydantic schemas for WebSocket messages — SCHEMA.md §16.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ConflictOut(BaseModel):
    """Conflict entry in TICK_UPDATE."""
    robot_ids: List[str]
    cell: Dict[str, int]          # {"x": int, "y": int}
    resolved_by: Optional[str]    # e.g. "AMR-03_yield"


class ObstacleOut(BaseModel):
    """Temporary obstacle entry in TICK_UPDATE."""
    obstacle_id: str
    position: Dict[str, int]      # {"x": int, "y": int}
    created_tick: int
    expires_at_tick: int


class TickUpdateMessage(BaseModel):
    """
    SCHEMA.md §16 — WebSocket tick payload.
    DO NOT alter field names.
    """
    type: str = "TICK_UPDATE"
    tick: int
    timestamp_ms: int
    robots: List[Dict[str, Any]]
    active_conflicts: List[ConflictOut]
    temporary_obstacles: List[ObstacleOut]


class ChaosToggleRequest(BaseModel):
    """POST /api/chaos/toggle — SCHEMA.md §17."""
    packet_loss_pct: int = 0
