"""
priority_gnn.py — Learned GNN-Tuned Priority with Mandatory Deterministic Fallback,
EMA Smoothing, Right-of-Way Locking, and Anti-Flapping Cooldown.

Trained from N simulated episodes, exported to NumPy for dependency-free edge inference.
Deterministic fallback in fallback_priority.py remains authoritative on any model failure,
latency spike, or out-of-bounds prediction.

Provides ML-adjusted priority arbitration bounded strictly within ±200 of baseline.
Guarantees graceful fallback to calculate_deterministic_priority and enforces cooldown windows.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

from app.ml.fallback_priority import (
    ARBITRATION_RADIUS,
    AUDIT_BASE_SCORE,
    AUDIT_MAX_CEILING,
    DEFAULT_EMA_ALPHA,
    EMA_ALPHA,
    FALLBACK_COOLDOWN_TICKS,
    GNN_CALIBRATION_OFFSET,
    AntiFlappingCooldown,
    RightOfWayTracker,
    apply_ema_smoothing,
    calculate_deterministic_priority,
)
from app.ml.priority_gnn_infer import (
    DEFAULT_HOP_RADIUS,
    DEFAULT_K_HOPS,
    MAX_GNN_LATENCY_MS,
    build_khop_neighborhood,
    build_local_conflict_graph,
    profile_graph_construction,
)

log = logging.getLogger(__name__)

MAX_GNN_ADJUSTMENT = 200.0


def compute_priority(
    robot: Any,
    task: Optional[Any],
    distance_to_goal: int,
    gnn_model: Optional[Any] = None,
    prev_score: Optional[float] = None,
    alpha: float = DEFAULT_EMA_ALPHA,
    current_tick: int = 0,
    cooldown_tracker: Optional[AntiFlappingCooldown] = None,
    latency_budget_ms: float = MAX_GNN_LATENCY_MS,
) -> float:
    """
    Computes arbitration priority with mandatory fallback to deterministic baseline.
    Supports EMA smoothing, right-of-way locking, and anti-flapping latency cooldown.
    """
    # 1. Deterministic baseline
    baseline = calculate_deterministic_priority(robot, task, distance_to_goal)

    if baseline <= AUDIT_MAX_CEILING:
        # Taskless / auditing robots always remain at the audit floor
        if prev_score is not None and prev_score <= AUDIT_MAX_CEILING:
            baseline = apply_ema_smoothing(baseline, prev_score, alpha=alpha)
        return float(min(AUDIT_MAX_CEILING, baseline))

    if gnn_model is None:
        if prev_score is not None and prev_score > AUDIT_MAX_CEILING:
            baseline = apply_ema_smoothing(baseline, prev_score, alpha=alpha)
        return float(baseline)

    # 2. Check Anti-Flapping Cooldown Window
    in_cooldown = False
    if cooldown_tracker is not None and cooldown_tracker.is_in_cooldown(current_tick):
        in_cooldown = True
    elif getattr(robot, "fallback_until_tick", 0) > current_tick:
        in_cooldown = True

    if in_cooldown:
        log.debug("compute_priority: In fallback cooldown at tick %d; using deterministic baseline.", current_tick)
        if prev_score is not None and prev_score > AUDIT_MAX_CEILING:
            baseline = apply_ema_smoothing(baseline, prev_score, alpha=alpha)
        return float(baseline)

    # 3. Attempt GNN model inference with latency monitoring and exception safety
    t0 = time.perf_counter()
    try:
        if callable(gnn_model):
            adjustment = float(gnn_model(robot, task, distance_to_goal))
        elif hasattr(gnn_model, "predict"):
            adjustment = float(gnn_model.predict(robot, task, distance_to_goal))
        else:
            adjustment = 0.0

        latency_ms = (time.perf_counter() - t0) * 1000.0

        # Enforce Latency Budget: Trip into fallback cooldown if inference stalls
        if latency_ms > latency_budget_ms:
            log.warning(
                "GNN latency %.2f ms exceeded budget %.2f ms at tick %d; tripping fallback for %d ticks.",
                latency_ms, latency_budget_ms, current_tick, FALLBACK_COOLDOWN_TICKS
            )
            if cooldown_tracker is not None:
                cooldown_tracker.trip_fallback(current_tick, FALLBACK_COOLDOWN_TICKS)
            if hasattr(robot, "fallback_until_tick"):
                robot.fallback_until_tick = current_tick + FALLBACK_COOLDOWN_TICKS
            if prev_score is not None and prev_score > AUDIT_MAX_CEILING:
                baseline = apply_ema_smoothing(baseline, prev_score, alpha=alpha)
            return float(baseline)

        # Validate finite float
        if math.isnan(adjustment) or math.isinf(adjustment):
            log.warning("GNN model returned non-finite adjustment (%s); falling back to baseline.", adjustment)
            if cooldown_tracker is not None:
                cooldown_tracker.trip_fallback(current_tick, FALLBACK_COOLDOWN_TICKS)
            if hasattr(robot, "fallback_until_tick"):
                robot.fallback_until_tick = current_tick + FALLBACK_COOLDOWN_TICKS
            if prev_score is not None and prev_score > AUDIT_MAX_CEILING:
                baseline = apply_ema_smoothing(baseline, prev_score, alpha=alpha)
            return float(baseline)

        # Clamp adjustment to ±200.0
        clamped_adj = max(-MAX_GNN_ADJUSTMENT, min(MAX_GNN_ADJUSTMENT, adjustment))
        raw_score = baseline + clamped_adj

        # 4. Apply EMA smoothing within the active task tier
        if prev_score is not None and prev_score > AUDIT_MAX_CEILING:
            score = apply_ema_smoothing(raw_score, prev_score, alpha=alpha)
        else:
            score = raw_score

        return float(score)

    except Exception as e:
        log.warning("GNN priority evaluation failed (%s); silently falling back to deterministic score.", e)
        if cooldown_tracker is not None:
            cooldown_tracker.trip_fallback(current_tick, FALLBACK_COOLDOWN_TICKS)
        if hasattr(robot, "fallback_until_tick"):
            robot.fallback_until_tick = current_tick + FALLBACK_COOLDOWN_TICKS
        if prev_score is not None:
            baseline = apply_ema_smoothing(baseline, prev_score, alpha=alpha)
        return float(baseline)
