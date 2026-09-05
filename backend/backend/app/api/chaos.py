"""
Chaos mode endpoints — SCHEMA.md §17.

POST /api/chaos/toggle

Chaos simulates network packet loss at the COMMUNICATION layer only.
The underlying simulation state remains deterministic and correct.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chaos", tags=["Chaos"])


class ChaosToggleRequest(BaseModel):
    """SCHEMA.md §17 — Chaos toggle payload."""
    packet_loss_pct: int = Field(default=0, ge=0, le=100)


class ChaosStatusOut(BaseModel):
    enabled: bool
    packet_loss_pct: int


@router.post(
    "/toggle",
    summary="Toggle chaos mode (simulated packet loss)",
    description=(
        "Enables/disables simulated WebSocket packet loss. "
        "The simulation state itself remains correct and deterministic. "
        "Chaos only affects what is SENT to clients. "
        "Set packet_loss_pct=0 to disable."
    ),
    response_model=ChaosStatusOut,
)
async def toggle_chaos(body: ChaosToggleRequest, request: Request) -> ChaosStatusOut:
    engine = request.app.state.engine
    enabled = body.packet_loss_pct > 0
    engine.set_chaos(enabled, body.packet_loss_pct)
    log.info("CHAOS_TOGGLE enabled=%s packet_loss=%d%%", enabled, body.packet_loss_pct)
    return ChaosStatusOut(enabled=enabled, packet_loss_pct=body.packet_loss_pct)


@router.get("/status", summary="Get current chaos mode status", response_model=ChaosStatusOut)
async def chaos_status(request: Request) -> ChaosStatusOut:
    engine = request.app.state.engine
    return ChaosStatusOut(
        enabled=engine._chaos_enabled,
        packet_loss_pct=engine._chaos_packet_loss,
    )
