"""
priority_gnn.py — Optional GNN-Tuned Priority with Mandatory Deterministic Fallback.

Provides ML-adjusted priority arbitration bounded strictly within ±200 of baseline.
Guarantees graceful, silent fallback to calculate_deterministic_priority on any error,
NaN, missing dependency, or out-of-bounds prediction.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from app.ml.fallback_priority import AUDIT_BASE_SCORE, calculate_deterministic_priority

log = logging.getLogger(__name__)

MAX_GNN_ADJUSTMENT = 200.0
AUDIT_MAX_CEILING = -500.0


def compute_priority(
    robot: Any,
    task: Optional[Any],
    distance_to_goal: int,
    gnn_model: Optional[Any] = None,
) -> float:
    """
    Computes arbitration priority with mandatory fallback to deterministic baseline.
    """
    # 1. Deterministic baseline
    baseline = calculate_deterministic_priority(robot, task, distance_to_goal)

    if gnn_model is None:
        return baseline

    # 2. Attempt GNN model inference with strict bounds and exception safety
    try:
        if callable(gnn_model):
            adjustment = float(gnn_model(robot, task, distance_to_goal))
        elif hasattr(gnn_model, "predict"):
            adjustment = float(gnn_model.predict(robot, task, distance_to_goal))
        else:
            adjustment = 0.0

        # Validate finite float
        if math.isnan(adjustment) or math.isinf(adjustment):
            log.warning("GNN model returned non-finite adjustment (%s); falling back to baseline.", adjustment)
            return baseline

        # Clamp adjustment to ±200.0
        clamped_adj = max(-MAX_GNN_ADJUSTMENT, min(MAX_GNN_ADJUSTMENT, adjustment))
        score = baseline + clamped_adj

        # Enforce Audit Priority Tier Floor: GNN adjustments must NEVER elevate an auditing
        # robot above the minimum possible score of any task-carrying robot.
        if baseline < -500.0:
            score = min(AUDIT_MAX_CEILING, score)

        return float(score)

    except Exception as e:
        log.warning("GNN priority evaluation failed (%s); silently falling back to deterministic score.", e)
        return baseline
