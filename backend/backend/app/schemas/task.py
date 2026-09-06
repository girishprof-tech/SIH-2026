"""
Pydantic schemas for task-related REST endpoints.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    x: int = Field(..., ge=0, le=29, description="Column (0–29)")
    y: int = Field(..., ge=0, le=29, description="Row (0–29)")


class TaskInjectRequest(BaseModel):
    """
    POST /api/task/inject — SCHEMA.md §17.
    """
    pickup: Position = Field(..., description="Pickup station coordinates")
    dropoff: Position = Field(..., description="Drop-off station coordinates")
    urgency: int = Field(..., ge=1, le=5, description="Task urgency 1 (low) – 5 (critical)")

    @field_validator("urgency")
    @classmethod
    def urgency_range(cls, v: int) -> int:
        if not (1 <= v <= 5):
            raise ValueError("urgency must be between 1 and 5")
        return v


class TaskOut(BaseModel):
    task_id: str
    pickup: Position
    dropoff: Position
    urgency: int
    status: str
    assigned_robot_id: Optional[str] = None
    created_tick: int


class JobRequest(BaseModel):
    """User-facing job request to drive dispatch without raw pickup/dropoff coords."""
    job_type: Literal["fetch_item", "sort_batch", "audit_checkpoint"]
    item_id: Optional[str] = None
    zone: Optional[str] = None
    urgency: int = Field(..., ge=1, le=5, description="Task urgency 1 (low) – 5 (critical)")

    @field_validator("urgency")
    @classmethod
    def urgency_range(cls, v: int) -> int:
        if not (1 <= v <= 5):
            raise ValueError("urgency must be between 1 and 5")
        return v


class JobOut(BaseModel):
    job_type: str
    robot_type: str
    task_id: Optional[str] = None
    audit_id: Optional[str] = None
    robot_id: Optional[str] = None
    status: str
    message: str
