"""
Pydantic schemas for task-related REST endpoints.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    x: int = Field(..., ge=0, description="Column (x >= 0)")
    y: int = Field(..., ge=0, description="Row (y >= 0)")


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
    task_type: Optional[str] = None
    target_shelf_id: Optional[str] = None
    return_to_home: Optional[bool] = None
    lease_expires_tick: Optional[int] = None
    unclaimed_reason: Optional[str] = None


class JobRequest(BaseModel):
    """User-facing job request to drive dispatch without raw pickup/dropoff coords."""
    job_type: str = Field(..., description="Task category: fetch_item, sort_batch, audit_checkpoint, relocate")
    item_id: Optional[str] = None
    sku: Optional[str] = None
    quantity: int = 1
    zone: Optional[str] = None
    urgency: int = Field(3, ge=1, le=5, description="Task urgency 1 (low) – 5 (critical)")

    # Step 6 adaptive task form parameters
    shelf_id: Optional[str] = None
    pickup: Optional[Position] = None
    dropoff: Optional[Position] = None
    return_to_home: bool = True
    source_gate: Optional[str] = None
    destination_chute: Optional[str] = None
    route_code: Optional[str] = None
    checkpoint: Optional[Position] = None
    target_robot_id: Optional[str] = None

    @field_validator("urgency")
    @classmethod
    def urgency_range(cls, v: int) -> int:
        if not (1 <= v <= 5):
            raise ValueError("urgency must be between 1 and 5")
        return v


class OrderRequest(BaseModel):
    """Direct SKU order request for G2P retrieval and autonomous sortation."""
    sku: str = Field(..., description="Requested item SKU")
    quantity: int = Field(1, ge=1, description="Quantity to pick")
    destination_gate: Optional[str] = Field(None, description="Exit gate destination")
    urgency: int = Field(3, ge=1, le=5, description="Order urgency 1-5")
    dropoff: Optional[Position] = None
    return_to_home: bool = True


class JobOut(BaseModel):
    job_type: str
    robot_type: str
    order_id: Optional[str] = None
    task_id: Optional[str] = None
    audit_id: Optional[str] = None
    robot_id: Optional[str] = None
    target_shelf_id: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[int] = None
    destination_gate: Optional[str] = None
    status: str
    message: str
    lease_expires_tick: Optional[int] = None
    unclaimed_reason: Optional[str] = None


