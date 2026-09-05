"""
Pydantic schemas for robot-related REST and WebSocket payloads.
These are the SERIALIZATION types — not the internal Robot model.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class PathNodeOut(BaseModel):
    x: int
    y: int
    t: int


class RobotOut(BaseModel):
    """SCHEMA.md §4 — full robot state as sent over WebSocket."""

    robot_id: str
    position: dict  # {"x": int, "y": int}
    heading: str
    state: str
    battery_pct: float
    current_task_id: Optional[str] = None
    priority_score: int
    last_updated_tick: int
    path: List[PathNodeOut] = Field(default_factory=list)
