"""
Pydantic schemas for obstacle REST endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ObstacleCreateRequest(BaseModel):
    """POST /api/obstacles — add a temporary obstacle."""
    obstacle_id: str = Field(..., description="Unique obstacle identifier, e.g. TEMP-01")
    x: int = Field(..., ge=0, le=29)
    y: int = Field(..., ge=0, le=29)
    duration_ticks: int = Field(default=20, ge=1, description="How many ticks the obstacle lasts")


class ObstacleOut(BaseModel):
    obstacle_id: str
    position: dict   # {"x": int, "y": int}
    created_tick: int
    expires_at_tick: int
