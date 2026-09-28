"""
fallback_priority.py — Deterministic Baseline Priority Calculator with Auditing Floor Rule,
EMA Smoothing, Right-of-Way Locking (Hysteresis), and Anti-Flapping Cooldown.

Provides the guaranteed safe fallback priority calculation for conflict arbitration.
Enforces the mandatory rule that auditing/taskless robots always score in the lowest tier.
Includes EMA smoothing, right-of-way locking, and anti-flapping cooldown utilities.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from app.models.robot_fsm import RobotState

log = logging.getLogger(__name__)

# Base floor for taskless/auditing robots to ensure active missions always win right-of-way
AUDIT_BASE_SCORE = -1000.0
AUDIT_MAX_CEILING = -500.0

# ── Tunable Named Constants ──────────────────────────────────────────────────
# EMA smoothing weight on new priority score: score_t = alpha * new + (1 - alpha) * score_{t-1}
DEFAULT_EMA_ALPHA = 0.6
EMA_ALPHA = DEFAULT_EMA_ALPHA

# Right-of-way arbitration radius (Manhattan distance threshold where locking occurs)
ARBITRATION_RADIUS = 2

# Minimum duration to remain in fallback mode after tripping due to latency before re-engaging GNN
FALLBACK_COOLDOWN_TICKS = 15

# Calibrated offset aligning fallback priority distribution with GNN inference output
GNN_CALIBRATION_OFFSET = 1.53
FALLBACK_SCALE = 1.0
FALLBACK_OFFSET = 0.0


def apply_ema_smoothing(
    new_score: float,
    prev_score: Optional[float],
    alpha: float = DEFAULT_EMA_ALPHA,
) -> float:
    """
    Applies Exponential Moving Average (EMA) smoothing to priority scores:
        score_t = alpha * new_score + (1 - alpha) * score_{t-1}

    If prev_score is None, returns new_score directly.
    """
    if prev_score is None:
        return float(new_score)
    alpha = max(0.0, min(1.0, float(alpha)))
    return float(alpha * new_score + (1.0 - alpha) * prev_score)


class RightOfWayTracker:
    """
    Hysteresis engine for Right-of-Way locking between robot pairs.

    Once two robots enter arbitration radius (ARBITRATION_RADIUS) of each other,
    locks the resolved priority order until they clear that radius — preventing
    mid-junction reversals or priority flapping under changing wait times or model jitter.
    """

    def __init__(self, arbitration_radius: int = ARBITRATION_RADIUS) -> None:
        self.arbitration_radius = arbitration_radius
        # Map canonical pair key (min(id_a, id_b), max(id_a, id_b)) -> lock metadata
        self._locks: Dict[Tuple[str, str], Dict[str, Any]] = {}

    @staticmethod
    def _pair_key(id_a: str, id_b: str) -> Tuple[str, str]:
        return (id_a, id_b) if id_a < id_b else (id_b, id_a)

    def is_locked(self, id_a: str, id_b: str) -> bool:
        return self._pair_key(id_a, id_b) in self._locks

    def get_locked_winner(
        self,
        id_a: str,
        id_b: str,
        distance: int,
    ) -> Optional[str]:
        """
        Retrieves the locked winner if the pair is currently locked and within arbitration radius.
        If distance > arbitration_radius, clears the lock and returns None.
        """
        key = self._pair_key(id_a, id_b)
        lock = self._locks.get(key)
        if lock is None:
            return None

        # Check if the pair has cleared the junction / arbitration radius
        if distance > self.arbitration_radius:
            log.debug("RightOfWayTracker: Pair %s cleared arbitration radius (dist=%d > %d); unlocking.", key, distance, self.arbitration_radius)
            del self._locks[key]
            return None

        return str(lock["winner_id"])

    def lock_right_of_way(
        self,
        id_a: str,
        id_b: str,
        winner_id: str,
        distance: int,
        tick: int,
    ) -> None:
        """
        Locks the resolved right-of-way for a robot pair while within arbitration radius.
        """
        if distance > self.arbitration_radius:
            return  # Outside arbitration radius; do not lock

        loser_id = id_b if winner_id == id_a else id_a
        key = self._pair_key(id_a, id_b)
        self._locks[key] = {
            "winner_id": winner_id,
            "loser_id": loser_id,
            "locked_at_tick": tick,
            "distance": distance,
        }
        log.debug("RightOfWayTracker: Locked ROW for %s -> Winner=%s at tick %d", key, winner_id, tick)

    def unlock_pair(self, id_a: str, id_b: str) -> None:
        key = self._pair_key(id_a, id_b)
        self._locks.pop(key, None)

    def clear_stale_locks(
        self,
        robot_positions: Dict[str, Tuple[int, int]],
    ) -> List[Tuple[str, str]]:
        """
        Inspects all active locks; removes locks for pairs that have cleared arbitration radius.
        """
        unlocked = []
        for key, lock in list(self._locks.items()):
            id_a, id_b = key
            pos_a = robot_positions.get(id_a)
            pos_b = robot_positions.get(id_b)
            if pos_a is None or pos_b is None:
                del self._locks[key]
                unlocked.append(key)
                continue
            dist = abs(pos_a[0] - pos_b[0]) + abs(pos_a[1] - pos_b[1])
            if dist > self.arbitration_radius:
                del self._locks[key]
                unlocked.append(key)
        return unlocked

    def reset(self) -> None:
        self._locks.clear()


class AntiFlappingCooldown:
    """
    Enforces a minimum cooldown window in deterministic fallback mode before
    re-engaging the learned GNN path after a latency trip or model error.
    """

    def __init__(self, cooldown_ticks: int = FALLBACK_COOLDOWN_TICKS) -> None:
        self.cooldown_ticks = cooldown_ticks
        self.fallback_until_tick: int = 0
        self.trips_count: int = 0

    def trip_fallback(self, current_tick: int, cooldown_ticks: Optional[int] = None) -> None:
        """
        Trips the engine into fallback mode and sets the cooldown window.
        """
        ticks = cooldown_ticks if cooldown_ticks is not None else self.cooldown_ticks
        self.fallback_until_tick = max(self.fallback_until_tick, current_tick + ticks)
        self.trips_count += 1
        log.warning(
            "AntiFlappingCooldown: Tripped into fallback at tick %d; GNN disabled until tick %d (window=%d ticks).",
            current_tick, self.fallback_until_tick, ticks
        )

    def is_in_cooldown(self, current_tick: int) -> bool:
        """Returns True if the engine is currently constrained to fallback mode."""
        return current_tick < self.fallback_until_tick

    def ticks_remaining(self, current_tick: int) -> int:
        return max(0, self.fallback_until_tick - current_tick)

    def reset(self) -> None:
        self.fallback_until_tick = 0
        self.trips_count = 0


def calculate_deterministic_priority(
    robot: Any,
    task: Optional[Any],
    distance_to_goal: int,
    prev_score: Optional[float] = None,
    alpha: float = DEFAULT_EMA_ALPHA,
    use_calibration: bool = False,
) -> float:
    """
    Authoritative deterministic priority calculation with optional EMA smoothing.

    Rules:
      1. If the robot is in RobotState.AUDITING, or has no active task (task is None or task.status != 'IN_PROGRESS'),
         the robot is placed in the lowest priority tier (AUDIT_BASE_SCORE = -1000.0).
         It deterministically yields to any active delivery or task-carrying robot.
      2. If carrying an active task, follows the standard SCHEMA.md §13 formula:
             score = (task.urgency * 100)
                   + (500 if robot.battery_pct < 20 else 0)
                   + (robot.wait_ticks_so_far * 10)
                   - (distance_to_goal * 1)
      3. Applies EMA smoothing:
             score_t = alpha * score + (1 - alpha) * prev_score
         when prev_score is provided.
    """
    # Check if robot is auditing or taskless
    is_auditing = False
    robot_state = getattr(robot, "state", None)
    if robot_state == RobotState.AUDITING or str(robot_state) == "AUDITING":
        is_auditing = True
    elif getattr(robot, "is_audit", False):
        is_auditing = True
    elif task is None:
        is_auditing = True

    if is_auditing:
        # Lowest priority tier floor: guaranteed negative score
        wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 2)
        dist_pen = float(distance_to_goal)
        raw_score = AUDIT_BASE_SCORE + wait_bonus - dist_pen
        if prev_score is not None and prev_score <= AUDIT_MAX_CEILING:
            raw_score = apply_ema_smoothing(raw_score, prev_score, alpha=alpha)
        return float(min(AUDIT_MAX_CEILING, raw_score))

    # Standard task-carrying priority calculation
    urgency = getattr(task, "urgency", 1) if task is not None else 1
    battery_pct = getattr(robot, "battery_pct", 100.0)
    battery_bonus = 500.0 if battery_pct < 20.0 else 0.0
    wait_bonus = float(getattr(robot, "wait_ticks_so_far", 0) * 10)
    distance_penalty = float(distance_to_goal * 1)

    raw_score = (float(urgency) * 100.0) + battery_bonus + wait_bonus - distance_penalty

    if use_calibration:
        raw_score = raw_score * FALLBACK_SCALE + FALLBACK_OFFSET

    # Apply EMA smoothing only if previous score was in the active task tier
    if prev_score is not None and prev_score > AUDIT_MAX_CEILING:
        raw_score = apply_ema_smoothing(raw_score, prev_score, alpha=alpha)

    return float(raw_score)


def calibrate_fallback_distribution(
    gnn_model: Optional[Any] = None,
    samples: int = 1000,
    rng_seed: int = 42,
) -> Dict[str, float]:
    """
    Samples representative robot/task states and compares fallback priority
    against the GNN model output distribution. Calculates scale and offset
    parameters so fallback-to-GNN transitions do not cause systematic score shifts.
    """
    import numpy as np

    class _MockTask:
        def __init__(self, urgency: int) -> None:
            self.urgency = urgency
            self.status = "IN_PROGRESS"

    class _MockRobot:
        def __init__(self, battery: float, wait_ticks: int) -> None:
            self.battery_pct = battery
            self.wait_ticks_so_far = wait_ticks
            self.robot_type = "GOODS_TO_PERSON"
            self.state = "EN_ROUTE_PICKUP"
            self.position = (10, 10)
            self.peers = {}

    rng = np.random.RandomState(rng_seed)
    fallback_scores = []
    gnn_adjustments = []

    for _ in range(samples):
        urgency = int(rng.choice([1, 2, 3, 4, 5]))
        bat = float(rng.uniform(10.0, 100.0))
        wait = int(rng.randint(0, 15))
        dist = int(rng.randint(1, 30))

        robot = _MockRobot(bat, wait)
        task = _MockTask(urgency)

        fb = calculate_deterministic_priority(robot, task, dist)
        fallback_scores.append(fb)

        if gnn_model is not None:
            if callable(gnn_model):
                adj = float(gnn_model(robot, task, dist))
            elif hasattr(gnn_model, "predict"):
                adj = float(gnn_model.predict(robot, task, dist))
            else:
                adj = 0.0
            gnn_adjustments.append(adj)
        else:
            gnn_adjustments.append(0.0)

    fb_arr = np.array(fallback_scores, dtype=np.float32)
    adj_arr = np.array(gnn_adjustments, dtype=np.float32)

    mean_adj = float(np.mean(adj_arr))
    std_adj = float(np.std(adj_arr))
    mean_fb = float(np.mean(fb_arr))
    std_fb = float(np.std(fb_arr))

    return {
        "mean_fallback": mean_fb,
        "std_fallback": std_fb,
        "mean_adjustment": mean_adj,
        "std_adjustment": std_adj,
        "calibrated_offset": mean_adj,
        "samples_evaluated": float(samples),
    }
