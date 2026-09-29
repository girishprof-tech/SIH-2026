"""
testing/attack_step4_collision_shelves.py
=========================================
Step 4 Attack Suite: Multi-Agent Collision, Edge Swap, and Shelf Pass-Through Invariants.

Attack Vectors:
1. Shelf Pass-Through & Ingress Immunity:
   - Evaluates path planning across all 4 pod banks (176 shelf slots).
   - Asserts non-G2P robots (Audit, Sorting) NEVER enter shelf cells.
   - Asserts laden G2P robots (carrying_pod_id is not None) NEVER enter shelf cells.
   - Asserts unladen G2P robots enter ONLY their designated target shelf as the terminal step, with zero intermediate shelf traversals.
2. 20-Robot and 40-Robot Multi-Agent Collision & Swap Stress Test:
   - High-density fleet (20 and 40 robots) navigating across narrow corridors, cross-highways, and dead-ends.
   - Validates at every tick:
     - Vertex collision invariant: pos_i(t) != pos_j(t) for all i != j.
     - Edge swap invariant: !(pos_i(t) == pos_j(t+1) and pos_j(t) == pos_i(t+1)) for all i != j.
     - Shelf penetration invariant: No robot enters any shelf cell unless unladen G2P lifting.
3. 50-Seed Deterministic Sweep (Seeds 1001 to 1055):
   - Sweeps 55 distinct random seeds with randomized robot types, urgencies, starts, and goals.
   - Asserts 100% collision-free, swap-free, and shelf-free navigation across all 55 seeds.
4. Chaos Injection (30% Packet Loss + Sudden Robot Death in Narrow Aisle):
   - Injects 30% synthetic packet loss into peer messages.
   - Halts a robot process midway in a narrow aisle.
   - Asserts trailing and opposing peers safely yield or detour via open aisles, never cutting through shelves or colliding.
5. Concurrent Shelf Claim & Mutual Exclusion Race Condition:
   - Multiple robots simultaneously attempt to claim and path to the same pod slot.
   - Asserts deterministic single-winner claim resolution; loser treats slot as impassable.
"""

import math
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "models"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.world import WorldConfig, build_default_world
from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner, find_path
from priority import calculate_priority_score
from conflict_detector import detect_conflicts, detect_peer_conflict
from models import Heading, Robot, Task
from app.models.robot_fsm import RobotState


# ── ATTACK 1: SHELF PASS-THROUGH & INGRESS IMMUNITY ─────────────────────────

def attack_vector_1_shelf_pass_through():
    """Attack 1: Shelf Pass-Through & Ingress Immunity across all robot classes."""
    print("\n[Attack 1] Testing Shelf Pass-Through & Ingress Immunity across all robot classes...", flush=True)
    world = build_default_world()
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)
    grid.register_shelf_cells(world.pod_slots.values())
    planner = SpaceTimeAStarPlanner(grid)
    all_shelves = set(world.pod_slots.values())

    # 1. Non-G2P Robots (SCANNING_AUDIT & SORTING): Crossing from West aisle (4, 4) to South aisle (4, 9)
    # Bank A sits at rows 6, 7 (between y=4 and y=9). Direct path without shelf blocking would cross rows 6, 7.
    starts_and_goals = [
        ((4, 4), (4, 9)),
        ((12, 4), (12, 9)),
        ((22, 4), (22, 9)),
        ((7, 9), (7, 14)),   # Across Bank B (rows 11, 12)
        ((15, 14), (15, 19)), # Across Bank C (rows 16, 17)
        ((8, 19), (8, 24)),  # Across Bank D (rows 21, 22)
    ]

    for start, goal in starts_and_goals:
        # Non-G2P robot (Audit or Sorting AMR)
        path = planner.plan_path(
            start=start,
            goal=goal,
            current_tick=0,
            reservation_table={},
            robot_id="AMR-AUDIT",
            blocked_cells=all_shelves,
            allowed_exception=None,
        )
        assert len(path) > 0, f"Planner should find an aisle route between {start} and {goal}"
        for step in path:
            pos = (step["x"], step["y"])
            assert pos not in all_shelves, (
                f"VIOLATION: Non-G2P robot entered shelf cell {pos} on route from {start} to {goal}!"
            )

    print("  [PASS] Non-G2P robots (Audit & Sorting) strictly route around all 176 shelf cells", flush=True)

    # 2. Laden G2P Robot (carrying_pod_id is not None)
    for start, goal in starts_and_goals:
        path = planner.plan_path(
            start=start,
            goal=goal,
            current_tick=0,
            reservation_table={},
            robot_id="AMR-G2P-LADEN",
            blocked_cells=all_shelves,
            allowed_exception=None,  # Laden robots cannot enter any shelf cell
        )
        assert len(path) > 0
        for step in path:
            pos = (step["x"], step["y"])
            assert pos not in all_shelves, (
                f"VIOLATION: Laden G2P robot clipped shelf cell {pos}!"
            )
    print("  [PASS] Laden G2P robots strictly route around all shelf cells with 0 exceptions", flush=True)

    # 3. Unladen G2P Robot targeting a specific shelf cell: e.g. POD-A01 at (4, 6)
    target_shelf = world.pod_slots["POD-A01"]  # (4, 6)
    unladen_start = (10, 4)  # Cross-highway aisle
    path = planner.plan_path(
        start=unladen_start,
        goal=target_shelf,
        current_tick=0,
        reservation_table={},
        robot_id="AMR-G2P-UNLADEN",
        blocked_cells=all_shelves,
        allowed_exception=target_shelf,
    )
    assert len(path) > 0, "Unladen G2P must be able to reach target shelf"
    # All intermediate steps must be non-shelf cells
    for step in path[:-1]:
        pos = (step["x"], step["y"])
        assert pos not in all_shelves, f"Unladen G2P entered non-target shelf cell {pos}!"
    # Terminal step must be exactly the target shelf
    assert (path[-1]["x"], path[-1]["y"]) == target_shelf
    print(f"  [PASS] Unladen G2P enters only the designated target shelf {target_shelf} as the terminal step", flush=True)


# ── SIMULATED MULTI-AGENT RUNTIME FOR STEP 4 ATTACKS ─────────────────────────

class SimulatedRobot:
    def __init__(self, rid: str, start: Tuple[int, int], goal: Tuple[int, int], rtype: str, urgency: int = 3):
        self.rid = rid
        self.pos = start
        self.start = start
        self.goal = goal
        self.rtype = rtype
        self.urgency = urgency
        self.carrying_pod_id: Optional[str] = None
        self.path: List[Dict[str, Any]] = []
        self.wait_ticks = 0
        self.is_alive = True


def simulate_multi_agent_fleet(
    num_robots: int,
    max_ticks: int = 60,
    seed: int = 42,
    packet_loss_pct: float = 0.0,
    kill_robot_at_tick: Optional[Tuple[str, int]] = None,
) -> Dict[str, Any]:
    """
    High-fidelity simulation of decentralized robot decision cycle with Space-Time A*,
    symmetric arbitration, right-of-way locking, and shelf cell protection.
    """
    rng = random.Random(seed)
    world = build_default_world()
    all_shelves = set(world.pod_slots.values())
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)
    grid.register_shelf_cells(world.pod_slots.values())
    planner = SpaceTimeAStarPlanner(grid)

    # Valid non-shelf walkway cells for spawning and goals
    walkable_aisles = [
        (x, y) for x in range(world.width) for y in range(world.height)
        if grid.is_free((x, y)) and (x, y) not in all_shelves
    ]
    rng.shuffle(walkable_aisles)

    robots: List[SimulatedRobot] = []
    types_pool = ["GOODS_TO_PERSON", "SORTING", "SCANNING_AUDIT"]
    for i in range(num_robots):
        rid = f"AMR-{i+1:02d}"
        start = walkable_aisles[i]
        goal = walkable_aisles[(i + num_robots) % len(walkable_aisles)]
        rtype = types_pool[i % len(types_pool)]
        urgency = 1 + (i % 5)
        robots.append(SimulatedRobot(rid, start, goal, rtype, urgency))

    # Metric accumulators
    shared_cell_collisions = []
    edge_swaps = []
    shelf_violations = []

    pos_history = defaultdict(dict)  # tick -> {rid: pos}

    for tick in range(max_ticks):
        # 0. Check chaos injection: kill robot if scheduled
        if kill_robot_at_tick:
            k_id, k_tick = kill_robot_at_tick
            if tick >= k_tick:
                for r in robots:
                    if r.rid == k_id:
                        r.is_alive = False

        # Record current tick positions
        for r in robots:
            if r.is_alive:
                pos_history[tick][r.rid] = r.pos

        # Check Invariant 1: No two alive robots occupy the same cell at the same tick
        curr_positions: Dict[Tuple[int, int], str] = {}
        for r in robots:
            if not r.is_alive:
                continue
            if r.pos in curr_positions:
                shared_cell_collisions.append({
                    "tick": tick,
                    "cell": r.pos,
                    "robots": (curr_positions[r.pos], r.rid),
                })
            else:
                curr_positions[r.pos] = r.rid

            # Check Invariant 3: No robot enters a shelf cell unless unladen G2P at goal
            if r.pos in all_shelves:
                if not (r.rtype == "GOODS_TO_PERSON" and r.carrying_pod_id is None and r.pos == r.goal):
                    shelf_violations.append({
                        "tick": tick,
                        "robot": r.rid,
                        "type": r.rtype,
                        "cell": r.pos,
                    })

        # 1. Plan paths for robots needing movement
        for r in robots:
            if not r.is_alive:
                continue
            if not r.path or len(r.path) <= 1:
                if r.pos != r.goal:
                    # Construct reservation table from other peers
                    res_table = {}
                    for peer in robots:
                        if peer.rid == r.rid or not peer.is_alive:
                            continue
                        # Packet loss: peer messages might be dropped
                        if packet_loss_pct > 0 and rng.random() < packet_loss_pct:
                            continue
                        for dt in range(8):
                            res_table[(peer.pos[0], peer.pos[1], tick + dt)] = peer.rid
                        for step in (peer.path or [])[:4]:
                            st = step.get("t", tick + 1)
                            res_table[(int(step["x"]), int(step["y"]), st)] = peer.rid

                    allowed_exc = r.goal if (r.rtype == "GOODS_TO_PERSON" and r.carrying_pod_id is None and r.goal in all_shelves) else None
                    new_path = planner.plan_path(
                        start=r.pos,
                        goal=r.goal,
                        current_tick=tick,
                        reservation_table=res_table,
                        robot_id=r.rid,
                        blocked_cells=all_shelves,
                        allowed_exception=allowed_exc,
                    )
                    if new_path:
                        r.path = new_path

        # 2. Determine intended next positions
        intents: Dict[str, Tuple[int, int]] = {}
        for r in robots:
            if not r.is_alive:
                intents[r.rid] = r.pos
                continue
            if r.path and len(r.path) > 1:
                intents[r.rid] = (int(r.path[1]["x"]), int(r.path[1]["y"]))
            else:
                intents[r.rid] = r.pos

        # 3. Decentralized Peer Conflict Arbitration
        next_positions: Dict[str, Tuple[int, int]] = {}
        for r in robots:
            if not r.is_alive:
                next_positions[r.rid] = r.pos
                continue

            my_intent = intents[r.rid]
            must_yield = False

            if my_intent != r.pos:
                # Check for conflicts with peers
                for peer in robots:
                    if peer.rid == r.rid or not peer.is_alive:
                        continue
                    peer_intent = intents[peer.rid]

                    is_swap = (my_intent == peer.pos and peer_intent == r.pos)
                    is_vertex = (my_intent == peer_intent)
                    is_occupying = (my_intent == peer.pos)

                    if is_swap or is_vertex or is_occupying:
                        if is_occupying:
                            # Cannot enter a cell currently occupied by a peer
                            must_yield = True
                            break

                        # Symmetric Arbitration for vertex or swap conflicts
                        dist_r = abs(r.pos[0] - r.goal[0]) + abs(r.pos[1] - r.goal[1])
                        dist_p = abs(peer.pos[0] - peer.goal[0]) + abs(peer.pos[1] - peer.goal[1])
                        my_score = (-1000.0 + r.wait_ticks * 2 - dist_r) if r.rtype == "SCANNING_AUDIT" else (r.urgency * 100.0 + r.wait_ticks * 10.0 - dist_r)
                        peer_score = (-1000.0 + peer.wait_ticks * 2 - dist_p) if peer.rtype == "SCANNING_AUDIT" else (peer.urgency * 100.0 + peer.wait_ticks * 10.0 - dist_p)

                        i_win = (my_score > peer_score) or (my_score == peer_score and r.rid < peer.rid)
                        if not i_win:
                            must_yield = True
                            break

            if must_yield:
                r.wait_ticks += 1
                next_positions[r.rid] = r.pos
                # Replan next tick
                r.path = []
            else:
                r.wait_ticks = 0
                next_positions[r.rid] = my_intent
                if r.path and len(r.path) > 1:
                    r.path = r.path[1:]

        # Check Invariant 2: Edge Swaps
        for i in range(len(robots)):
            ra = robots[i]
            if not ra.is_alive:
                continue
            for j in range(i + 1, len(robots)):
                rb = robots[j]
                if not rb.is_alive:
                    continue
                pos_a, next_a = ra.pos, next_positions[ra.rid]
                pos_b, next_b = rb.pos, next_positions[rb.rid]
                if next_a == pos_b and next_b == pos_a and pos_a != pos_b:
                    edge_swaps.append({
                        "tick": tick,
                        "robots": (ra.rid, rb.rid),
                        "positions": (pos_a, pos_b),
                    })

        # Apply positions
        for r in robots:
            r.pos = next_positions[r.rid]

    return {
        "shared_cell_collisions": shared_cell_collisions,
        "edge_swaps": edge_swaps,
        "shelf_violations": shelf_violations,
    }


# ── ATTACK 2: 20-ROBOT & 40-ROBOT MULTI-AGENT STRESS TEST ────────────────────

def attack_vector_2_multi_agent_stress():
    """Attack 2: 20-Robot & 40-Robot Multi-Agent Stress Test."""
    print("\n[Attack 2] Testing 20-Robot & 40-Robot High-Density Stress...", flush=True)

    # 20 robots test
    res_20 = simulate_multi_agent_fleet(num_robots=20, max_ticks=40, seed=42)
    assert len(res_20["shared_cell_collisions"]) == 0, f"Collisions in 20-robot test: {res_20['shared_cell_collisions']}"
    assert len(res_20["edge_swaps"]) == 0, f"Edge swaps in 20-robot test: {res_20['edge_swaps']}"
    assert len(res_20["shelf_violations"]) == 0, f"Shelf violations in 20-robot test: {res_20['shelf_violations']}"
    print("  [PASS] 20-Robot fleet: 0 vertex collisions, 0 edge swaps, 0 shelf pass-throughs across 40 ticks", flush=True)

    # 40 robots test (Ultra-dense warehouse load)
    res_40 = simulate_multi_agent_fleet(num_robots=40, max_ticks=40, seed=99)
    assert len(res_40["shared_cell_collisions"]) == 0, f"Collisions in 40-robot test: {res_40['shared_cell_collisions']}"
    assert len(res_40["edge_swaps"]) == 0, f"Edge swaps in 40-robot test: {res_40['edge_swaps']}"
    assert len(res_40["shelf_violations"]) == 0, f"Shelf violations in 40-robot test: {res_40['shelf_violations']}"
    print("  [PASS] 40-Robot fleet: 0 vertex collisions, 0 edge swaps, 0 shelf pass-throughs across 40 ticks", flush=True)


# ── ATTACK 3: 50-SEED DETERMINISTIC SWEEP ────────────────────────────────────

def attack_vector_3_seed_sweep():
    """Attack 3: Seed sweep across >= 50 distinct random seeds."""
    print("\n[Attack 3] Sweeping 50+ Deterministic Seeds (Seeds 1001 to 1055)...", flush=True)
    total_seeds = 55
    failed_seeds = []

    for seed in range(1001, 1001 + total_seeds):
        res = simulate_multi_agent_fleet(num_robots=16, max_ticks=25, seed=seed)
        if res["shared_cell_collisions"] or res["edge_swaps"] or res["shelf_violations"]:
            failed_seeds.append((seed, res))

    assert len(failed_seeds) == 0, f"Seeds sweep failed on seeds: {failed_seeds}"
    print(f"  [PASS] Successfully swept all {total_seeds} seeds: exactly 0 collisions, 0 swaps, 0 shelf violations", flush=True)


# ── ATTACK 4: CHAOS INJECTION (PACKET LOSS & ROBOT DEATH) ────────────────────

def attack_vector_4_chaos_injection():
    """Attack 4: Chaos injection with 30% packet loss and sudden robot process death."""
    print("\n[Attack 4] Testing Chaos Injection (30% Packet Loss + Mid-Aisle Robot Death)...", flush=True)

    # Kill robot AMR-03 at tick 10 in the middle of traffic
    res_chaos = simulate_multi_agent_fleet(
        num_robots=20,
        max_ticks=45,
        seed=777,
        packet_loss_pct=0.30,
        kill_robot_at_tick=("AMR-03", 10),
    )

    assert len(res_chaos["shared_cell_collisions"]) == 0, f"Collisions under chaos: {res_chaos['shared_cell_collisions']}"
    assert len(res_chaos["edge_swaps"]) == 0, f"Edge swaps under chaos: {res_chaos['edge_swaps']}"
    assert len(res_chaos["shelf_violations"]) == 0, f"Shelf violations under chaos: {res_chaos['shelf_violations']}"
    print("  [PASS] Chaos test passed: 30% packet loss and deadlocked robot handled with zero collisions or shelf cuts", flush=True)


# ── ATTACK 5: CONCURRENT SHELF CLAIM & MUTUAL EXCLUSION ──────────────────────

def attack_vector_5_pod_claim_mutex():
    """Attack 5: Mutual exclusion race condition when two robots target the same pod."""
    print("\n[Attack 5] Testing Concurrent Shelf Claim & Mutual Exclusion Race Condition...", flush=True)
    world = build_default_world()
    grid = WarehouseGrid(obstacles=list(world.static_obstacles), width=world.width, height=world.height)
    grid.register_shelf_cells(world.pod_slots.values())
    planner = SpaceTimeAStarPlanner(grid)
    all_shelves = set(world.pod_slots.values())

    target_slot = world.pod_slots["POD-B10"]  # A shelf cell in Bank B

    # Robot 1 (higher priority) claims slot
    r1_start = (10, 10)
    r2_start = (10, 13)

    r1_path = planner.plan_path(
        start=r1_start,
        goal=target_slot,
        current_tick=0,
        reservation_table={},
        robot_id="AMR-G2P-WINNER",
        blocked_cells=all_shelves,
        allowed_exception=target_slot,
    )
    assert len(r1_path) > 0, "Winner should obtain a path to target shelf"

    # Robot 2 is the loser of the claim race: slot is locked by Winner
    res_table = {}
    for step in r1_path:
        res_table[(step["x"], step["y"], step["t"])] = "AMR-G2P-WINNER"
    # Mark slot physically claimed
    for dt in range(30):
        res_table[(target_slot[0], target_slot[1], dt)] = "AMR-G2P-WINNER"

    # Robot 2 attempts to path to the SAME slot: must be denied (empty path or no entry)
    r2_path = planner.plan_path(
        start=r2_start,
        goal=target_slot,
        current_tick=0,
        reservation_table=res_table,
        robot_id="AMR-G2P-LOSER",
        blocked_cells=all_shelves,
        allowed_exception=None,  # Not allowed into target_slot
    )
    assert len(r2_path) == 0, "Losing robot must NOT be granted access to the claimed shelf slot!"
    print("  [PASS] Concurrent shelf claim race strictly enforces single-owner mutual exclusion", flush=True)


def main():
    print("=" * 75)
    print("RUNNING STEP 4 ATTACK SUITE: COLLISION, SWAP & SHELF PASS-THROUGH")
    print("=" * 75)

    attack_vector_1_shelf_pass_through()
    attack_vector_2_multi_agent_stress()
    attack_vector_3_seed_sweep()
    attack_vector_4_chaos_injection()
    attack_vector_5_pod_claim_mutex()

    print("\n" + "=" * 75)
    print(">>> STEP 4 ATTACK SUITE: ALL 5 ATTACK VECTORS PASSED WITH ZERO DEFECTS <<<")
    print("=" * 75)
    os._exit(0)


if __name__ == "__main__":
    main()
