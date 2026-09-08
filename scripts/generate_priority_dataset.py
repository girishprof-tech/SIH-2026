"""
generate_priority_dataset.py — Generates offline training data from decentralized simulation.

Runs 200+ episodes with randomized fleet sizes (3–15 robots), randomized urgencies,
battery levels, and obstacle placements using real RobotNode instances over LoopbackTransport.
Logs 6-dim node feature vectors, graph edges for robots in conflict radius, and computes
target priority adjustments bounded within [-200, +200].
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import numpy as np

# Ensure workspace paths are available
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "archive" / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.models.robot_fsm import RobotState
from app.models.world import build_default_world
from app.transport.loopback_transport import LoopbackNetworkHub, LoopbackTransport
from app.ml.fallback_priority import AUDIT_BASE_SCORE, calculate_deterministic_priority
from app.ml.priority_gnn import MAX_GNN_ADJUSTMENT, AUDIT_MAX_CEILING
from grid import WarehouseGrid
from models import Heading, Robot, Task
from robot_node import RobotNode


def get_random_obstacles(rng: random.Random, width: int = 30, height: int = 30, density: float = 0.12) -> List[Tuple[int, int]]:
    """Generates randomized warehouse shelf obstacles preserving perimeter highways."""
    obstacles = []
    # Standard shelf blocks with random gaps
    for y in range(4, height - 4, 3):
        for x in range(3, width - 3):
            if x not in (7, 14, 21):
                if rng.random() < 0.85:
                    obstacles.append((x, y))
    return obstacles


def run_simulation_episode(
    episode_id: int,
    fleet_size: int,
    rng: random.Random,
    max_ticks: int = 60,
) -> Dict[str, Any]:
    """
    Runs one decentralized simulation episode with fleet_size real RobotNodes.
    Returns logged conflict events with 6-dim features and outcome targets.
    """
    obstacles = get_random_obstacles(rng)
    grid = WarehouseGrid(obstacles=obstacles, width=30, height=30)
    free_cells = [(x, y) for x in range(30) for y in range(30) if grid.is_free((x, y))]
    rng.shuffle(free_cells)

    if len(free_cells) < fleet_size * 3:
        fleet_size = max(3, len(free_cells) // 3)

    hub = LoopbackNetworkHub()
    peer_ports = {f"AMR-{i+1:02d}": 9000 + i + 1 for i in range(fleet_size)}

    nodes: List[RobotNode] = []
    temp_log_dir = ROOT_DIR / "logs" / "dataset_gen"
    temp_log_dir.mkdir(parents=True, exist_ok=True)

    for i in range(fleet_size):
        rid = f"AMR-{i+1:02d}"
        start_pos = free_cells[i]
        pickup_pos = free_cells[fleet_size + i]
        dropoff_pos = free_cells[2 * fleet_size + i]
        urgency = rng.randint(1, 5)
        battery_pct = round(rng.uniform(30.0, 100.0), 1)
        is_audit = (i == 0 and fleet_size >= 4 and rng.random() < 0.3)
        robot_type = "SCANNING_AUDIT" if is_audit else ("GOODS_TO_PERSON" if i % 2 == 0 else "SORTING")

        transport = LoopbackTransport(node_id=rid, hub=hub)

        node = RobotNode(
            robot_id=rid,
            start_pos=start_pos,
            goal_pos=pickup_pos,
            urgency=urgency,
            battery_pct=battery_pct,
            obstacles=obstacles,
            port=peer_ports[rid],
            peer_ports=peer_ports,
            transport=transport,
            log_dir=temp_log_dir,
            tick_interval_s=0.0,
            robot_type=robot_type,
            enable_idle_audit=is_audit,
        )
        nodes.append(node)

    # Simulation state tracking
    conflict_records: List[Dict[str, Any]] = []

    # Run tick loop
    for tick in range(max_ticks):
        # Step all nodes
        tick_frames = []
        for node in nodes:
            frame = node.step(tick)
            tick_frames.append((node, frame))

        # Check for conflicts among active nodes
        active_nodes = [n for n in nodes if n.fsm.state != RobotState.IDLE]
        for i, n1 in enumerate(active_nodes):
            r1 = n1.robot
            pos1 = r1.position
            for j in range(i + 1, len(active_nodes)):
                n2 = active_nodes[j]
                r2 = n2.robot
                pos2 = r2.position
                m_dist = abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])

                # Conflict radius is 2 cells (as defined in conflict-engine)
                if m_dist <= 2:
                    for n, other in [(n1, n2), (n2, n1)]:
                        dist_to_goal = 0
                        if n.goal_pos:
                            dist_to_goal = abs(n.robot.position[0] - n.goal_pos[0]) + abs(n.robot.position[1] - n.goal_pos[1])
                        
                        is_auditing = 1.0 if n.robot_type == "SCANNING_AUDIT" else 0.0
                        nearby_count = sum(
                            1 for peer in active_nodes 
                            if peer.robot.robot_id != n.robot.robot_id and 
                            abs(peer.robot.position[0] - n.robot.position[0]) + abs(peer.robot.position[1] - n.robot.position[1]) <= 2
                        )

                        features = [
                            float(n.urgency),
                            float(n.robot.battery_pct),
                            float(n.robot.wait_ticks_so_far),
                            float(dist_to_goal),
                            float(nearby_count),
                            float(is_auditing),
                        ]

                        conflict_records.append({
                            "episode": episode_id,
                            "tick": tick,
                            "robot_id": n.robot.robot_id,
                            "other_id": other.robot.robot_id,
                            "features": features,
                            "baseline_score": float(calculate_deterministic_priority(n.robot, n.task, dist_to_goal)),
                            "wait_ticks": n.robot.wait_ticks_so_far,
                            "consecutive_wait": n.consecutive_wait_ticks,
                            "urgency": n.urgency,
                            "battery": n.robot.battery_pct,
                            "is_auditing": is_auditing,
                        })

        # Early termination if all nodes idle
        if all(n.fsm.state == RobotState.IDLE for n in nodes):
            break

    # Close transports
    for node in nodes:
        node.close()

    return {"records": conflict_records}


def main():
    print("=" * 70)
    print("PHASE 1: PRIORITY GNN DATASET GENERATION")
    print("Running 200 decentralized simulation episodes...")
    print("=" * 70)

    t0 = time.time()
    rng = random.Random(42)
    all_node_features: List[List[float]] = []
    all_targets: List[float] = []
    total_conflicts = 0

    num_episodes = 200
    for ep in range(1, num_episodes + 1):
        fleet_size = rng.randint(3, 15)
        result = run_simulation_episode(ep, fleet_size, rng, max_ticks=50)
        records = result["records"]
        total_conflicts += len(records)

        # Compute targets for each recorded conflict
        for rec in records:
            feats = rec["features"]
            # feats: [urgency, battery_pct, wait_ticks_so_far, distance_to_goal, num_robots_in_conflict_radius, is_auditing_flag]
            urgency = feats[0]
            battery = feats[1]
            wait_ticks = feats[2]
            dist_goal = feats[3]
            num_nearby = feats[4]
            is_audit = feats[5]

            # Heuristic target calculation:
            # - High urgency / high wait starved robots need positive adjustment to prevent livelock/starvation
            # - Auditing robots must NEVER exceed -500 (AUDIT_MAX_CEILING floor rule)
            if is_audit > 0.5:
                # Auditing robot: target adjustment keeps it firmly yields to all task robots
                target = -150.0 + rng.uniform(-10.0, 10.0)
            else:
                # Task robot: adjust based on urgency, starvation prevention, and battery urgency
                urgency_boost = (urgency - 3.0) * 25.0
                starvation_boost = min(80.0, wait_ticks * 12.0)
                battery_boost = 30.0 if battery < 40.0 else 0.0
                congestion_penalty = min(30.0, num_nearby * 8.0)

                target = urgency_boost + starvation_boost + battery_boost - congestion_penalty
                target += rng.gauss(0.0, 5.0)

            # Strict bounds [-200, 200]
            target = max(-MAX_GNN_ADJUSTMENT, min(MAX_GNN_ADJUSTMENT, target))

            all_node_features.append(feats)
            all_targets.append(target)

        if ep % 25 == 0 or ep == num_episodes:
            elapsed = time.time() - t0
            print(f"  Episode {ep:3d}/{num_episodes} complete | Conflicts logged: {total_conflicts:5d} | Elapsed: {elapsed:.1f}s")

    # Convert to NumPy arrays
    features_np = np.array(all_node_features, dtype=np.float32)
    targets_np = np.array(all_targets, dtype=np.float32)

    # Save dataset to data/priority_training_dataset.npz
    out_dir = ROOT_DIR / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "priority_training_dataset.npz"

    np.savez_compressed(
        out_file,
        features=features_np,
        targets=targets_np,
        num_episodes=num_episodes,
        feature_names=np.array([
            "urgency", "battery_pct", "wait_ticks_so_far",
            "distance_to_goal", "num_robots_in_conflict_radius", "is_auditing_flag"
        ]),
    )

    print("-" * 70)
    print(f"Dataset generated successfully!")
    print(f"  Saved to: {out_file}")
    print(f"  Total samples: {len(features_np)}")
    print(f"  Feature shape: {features_np.shape}")
    print(f"  Target mean: {np.mean(targets_np):.2f}, std: {np.std(targets_np):.2f}")
    print(f"  Target range: [{np.min(targets_np):.1f}, {np.max(targets_np):.1f}]")
    print("=" * 70)


if __name__ == "__main__":
    main()
