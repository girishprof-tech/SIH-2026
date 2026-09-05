"""
Central configuration for the SIH2026 backend.

All values default to the SCHEMA.md specification.
Override via environment variables or .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Server ────────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # ── Simulation clock ─────────────────────────────────────────────────────
    # SCHEMA.md: TICK_MS = 500
    SIM_TICK_MS: int = Field(default=500, ge=50, le=5000)

    # ── World / grid ─────────────────────────────────────────────────────────
    # SCHEMA.md: GRID_WIDTH = 30, GRID_HEIGHT = 30
    GRID_WIDTH: int = 30
    GRID_HEIGHT: int = 30
    CELL_SIZE_M: float = 1.0

    # ── Robot physics (SCHEMA.md exact values) ────────────────────────────────
    ROBOT_SPEED_TILES_PER_TICK: int = 1
    TURN_COST_TICKS: int = 1

    # ── Battery (SCHEMA.md exact values) ─────────────────────────────────────
    BATTERY_MOVE_COST: float = 1.0    # % per move
    BATTERY_TURN_COST: float = 0.5    # % per turn
    BATTERY_WAIT_COST: float = 0.1    # % per wait tick
    BATTERY_CHARGE_RATE: float = 5.0  # % per tick
    BATTERY_LOW_THRESHOLD: float = 20.0
    BATTERY_CHARGE_TARGET: float = 80.0
    BATTERY_MAX: float = 100.0
    BATTERY_MIN: float = 0.0

    # ── Fleet (SCHEMA.md: fleet_size=10, prefix="AMR") ────────────────────────
    FLEET_SIZE: int = 10
    ROBOT_PREFIX: str = "AMR"

    # ── Conflict detection radius (SCHEMA.md: 2-cell) ─────────────────────────
    CONFLICT_RADIUS: int = 2

    # ── Planner (pluggable, mock by default) ──────────────────────────────────
    PLANNER_BACKEND: str = "mock"   # "mock" | "external"
    PLANNER_URL: str = ""           # URL if PLANNER_BACKEND == "external"

    # ── WebSocket backpressure ────────────────────────────────────────────────
    # Clients that cannot keep up after this many queued messages are dropped.
    WS_MAX_QUEUE: int = 16

    # ── Chaos mode ────────────────────────────────────────────────────────────
    CHAOS_ENABLED: bool = False
    CHAOS_PACKET_LOSS_PCT: int = 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
