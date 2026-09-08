"""
test_priority_fallback.py — Unit tests for GNN Priority Fallback and Auditing Priority Floor.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.ml.fallback_priority import AUDIT_BASE_SCORE, calculate_deterministic_priority
from app.ml.priority_gnn import compute_priority
from app.models.robot_fsm import RobotState
from models import Heading, Robot, Task


def make_robot(
    robot_id: str = "AMR-01",
    state: RobotState = RobotState.EN_ROUTE_PICKUP,
    battery_pct: float = 80.0,
    wait_ticks: int = 0,
) -> Robot:
    return Robot(
        robot_id=robot_id,
        position=(5, 5),
        heading=Heading.NORTH,
        state=state,
        battery_pct=battery_pct,
        current_task_id="TASK-01",
        wait_ticks_so_far=wait_ticks,
        priority_score=0.0,
    )


def make_task(urgency: int = 3, status: str = "IN_PROGRESS") -> Task:
    return Task(
        task_id="TASK-01",
        pickup=(2, 2),
        dropoff=(10, 10),
        urgency=urgency,
        created_tick=0,
        status=status,
    )


def test_model_exception_fallback():
    """GNN model raising an exception falls back silently to deterministic score."""
    robot = make_robot()
    task = make_task(urgency=3)
    dist = 10

    def faulty_model(*args, **kwargs):
        raise RuntimeError("GNN model inference crashed on CUDA device!")

    expected_baseline = calculate_deterministic_priority(robot, task, dist)
    actual_score = compute_priority(robot, task, dist, gnn_model=faulty_model)

    assert actual_score == expected_baseline


def test_model_nan_fallback():
    """GNN model returning NaN falls back silently to deterministic score."""
    robot = make_robot()
    task = make_task(urgency=4)
    dist = 8

    def nan_model(*args, **kwargs):
        return float("nan")

    expected_baseline = calculate_deterministic_priority(robot, task, dist)
    actual_score = compute_priority(robot, task, dist, gnn_model=nan_model)

    assert actual_score == expected_baseline
    assert not math.isnan(actual_score)


def test_model_out_of_range_clamped():
    """GNN adjustment outside ±200 is clamped strictly within ±200."""
    robot = make_robot()
    task = make_task(urgency=2)
    dist = 5

    def wild_positive_model(*args, **kwargs):
        return 9999.0

    def wild_negative_model(*args, **kwargs):
        return -9999.0

    baseline = calculate_deterministic_priority(robot, task, dist)
    pos_score = compute_priority(robot, task, dist, gnn_model=wild_positive_model)
    neg_score = compute_priority(robot, task, dist, gnn_model=wild_negative_model)

    assert pos_score == baseline + 200.0
    assert neg_score == baseline - 200.0


def test_auditing_robot_lowest_priority_tier_floor():
    """
    Mandatory Rule: Auditing robot (taskless or state=AUDITING) MUST score in lowest tier
    and deterministically lose arbitration against any task-carrying robot (e.g. EN_ROUTE_DROPOFF).
    """
    # 1. Delivery robot carrying active delivery payload
    delivery_robot = make_robot(
        robot_id="AMR-01",
        state=RobotState.EN_ROUTE_DROPOFF,
        battery_pct=50.0,
        wait_ticks=0,
    )
    delivery_task = make_task(urgency=1, status="IN_PROGRESS")  # Lowest possible task urgency

    # 2. Auditing patrol robot with high wait ticks
    audit_robot = make_robot(
        robot_id="AMR-02",
        state=RobotState.AUDITING,
        battery_pct=90.0,
        wait_ticks=25,
    )

    delivery_score = calculate_deterministic_priority(delivery_robot, delivery_task, distance_to_goal=25)
    audit_score = calculate_deterministic_priority(audit_robot, task=None, distance_to_goal=1)

    # Auditing robot MUST score in negative lowest tier (AUDIT_BASE_SCORE = -1000)
    assert audit_score < -500.0
    # Delivery robot with task MUST score strictly higher than audit robot
    assert delivery_score > audit_score
    # Margin must be substantial (> 500 points)
    assert delivery_score - audit_score > 500.0


def test_auditing_robot_with_gnn_cannot_leapfrog_task_robot():
    """
    Even with maximal positive GNN adjustment (+200), an auditing robot
    can NEVER surpass the score of an active task robot.
    """
    delivery_robot = make_robot(robot_id="AMR-01", state=RobotState.EN_ROUTE_DROPOFF)
    delivery_task = make_task(urgency=1)

    audit_robot = make_robot(robot_id="AMR-02", state=RobotState.AUDITING)

    def max_boost_model(*args, **kwargs):
        return 200.0

    delivery_score = compute_priority(delivery_robot, delivery_task, distance_to_goal=20)
    boosted_audit_score = compute_priority(audit_robot, task=None, distance_to_goal=1, gnn_model=max_boost_model)

    assert boosted_audit_score <= -500.0
    assert delivery_score > boosted_audit_score


def test_numpy_pytorch_numerical_parity():
    """
    Verifies numerical parity between NumPy edge inference engine and PyTorch/mathematical forward pass
    across 20 fixed sample inputs with tolerance < 1e-4.
    """
    import numpy as np
    from app.ml.priority_gnn_infer import PriorityGNNInfer

    infer = PriorityGNNInfer()

    # Generate 20 fixed pseudo-random feature vectors
    rng = np.random.RandomState(1337)
    sample_inputs = rng.uniform(
        low=[1.0, 20.0, 0.0, 1.0, 0.0, 0.0],
        high=[5.0, 100.0, 30.0, 50.0, 5.0, 1.0],
        size=(20, 6),
    ).astype(np.float32)

    # 1. Forward pass with NumPy infer engine
    numpy_outputs = infer.forward(sample_inputs)

    # 2. Forward pass with PyTorch (if torch installed) or explicit manual layer math
    try:
        import torch
        import torch.nn as nn

        w1_torch = torch.from_numpy(infer.W_self1.T)
        b1_torch = torch.from_numpy(infer.b1)
        w2_torch = torch.from_numpy(infer.W_self2.T)
        b2_torch = torch.from_numpy(infer.b2)
        w_out_torch = torch.from_numpy(infer.W_out.T)
        b_out_torch = torch.from_numpy(infer.b_out)

        x_torch = torch.from_numpy(sample_inputs)
        h1 = torch.relu(torch.matmul(x_torch, w1_torch.T) + b1_torch)
        h2 = torch.relu(torch.matmul(h1, w2_torch.T) + b2_torch)
        ref_outputs = (200.0 * torch.tanh((torch.matmul(h2, w_out_torch.T) + b_out_torch) / 50.0)).numpy()
    except ImportError:
        # Exact reference math
        h1 = np.maximum(0.0, np.dot(sample_inputs, infer.W_self1) + infer.b1)
        h2 = np.maximum(0.0, np.dot(h1, infer.W_self2) + infer.b2)
        ref_outputs = 200.0 * np.tanh((np.dot(h2, infer.W_out) + infer.b_out) / 50.0)

    # Assert numerical parity tolerance < 1e-4
    max_diff = float(np.max(np.abs(numpy_outputs - ref_outputs)))
    assert max_diff < 1e-4, f"Numerical divergence between NumPy and reference forward pass: {max_diff}"
    assert numpy_outputs.shape == (20, 1)


def test_robot_node_live_gnn_model_wired():
    """
    Verifies that RobotNode loads a real instance of PriorityGNNInfer (not None),
    and in a live simulation run across 100 ticks, non-zero GNN adjustments appear
    in priority scores.
    """
    from app.services.robot_node import RobotNode
    from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
    from app.ml.priority_gnn_infer import PriorityGNNInfer

    hub = LoopbackNetworkHub()
    transport = LoopbackTransport(node_id="AMR-TEST", hub=hub)

    node = RobotNode(
        robot_id="AMR-TEST",
        start_pos=(2, 2),
        goal_pos=(15, 15),
        urgency=4,
        battery_pct=85.0,
        obstacles=[],
        port=9999,
        peer_ports={"AMR-TEST": 9999},
        transport=transport,
        tick_interval_s=0.0,
    )

    # Assert model is a real instance of PriorityGNNInfer (not None)
    assert node.gnn_model is not None, "RobotNode.gnn_model must not be None in production"
    assert isinstance(node.gnn_model, PriorityGNNInfer)

    adjustments_observed = []
    for tick in range(100):
        frame = node.step(tick)
        dist_to_goal = 0
        if node.goal_pos:
            dist_to_goal = abs(node.robot.position[0] - node.goal_pos[0]) + abs(node.robot.position[1] - node.goal_pos[1])
        baseline = calculate_deterministic_priority(node.robot, node.task, dist_to_goal)
        current_score = node.robot.priority_score
        adj = current_score - baseline
        adjustments_observed.append(adj)

    node.close()

    # Verify at least once across 100 ticks a non-zero adjustment appeared
    non_zero_adjustments = [a for a in adjustments_observed if abs(a) > 1e-3]
    assert len(non_zero_adjustments) > 0, "Expected non-zero GNN priority adjustment in live 100-tick run"

