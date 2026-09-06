"""
test_fuzz_safety.py
Property-based fuzz testing harness for SIH-2026 Fleet Coordination.

Proves two critical safety invariants across hundreds of randomized scenarios:
  1. No-collision invariant: No two robots ever share a cell, and no head-on swap conflict occurs.
  2. No-starvation invariant: No robot is indefinitely blocked; all active robots reach their goals.

Uses the Hypothesis library with @settings(max_examples=500, deadline=None).
Directly imports Member 2 and Member 3 implementations from the codebase.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from hypothesis import given, settings, strategies as st

# Setup import paths to point to actual project code
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "testing"))

# Member 2 imports (Pathfinding)
from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner, find_path
from reservations import reserve_path, release_reservations, prune_past

# Member 3 imports (Conflict Detection & Arbitration)
from priority import calculate_priority_score
from conflict_detector import detect_conflicts
from arbitration import resolve_conflict
from conflict_engine import run_conflict_engine_tick
from models import Heading, Robot, RobotState, Task

# Static Map layout from existing codebase
from full_integration_test import get_static_shelves

# ---------------------------------------------------------------------------
# Map Initialization & Open Cell Extraction
# ---------------------------------------------------------------------------
REAL_OBSTACLES = get_static_shelves()
WAREHOUSE_GRID = WarehouseGrid(obstacles=REAL_OBSTACLES, width=30, height=30)
list_of_open_cells: List[Tuple[int, int]] = [
    (x, y)
    for x in range(WAREHOUSE_GRID.width)
    for y in range(WAREHOUSE_GRID.height)
    if WAREHOUSE_GRID.is_free((x, y))
]

# ---------------------------------------------------------------------------
# Hypothesis Strategies
# ---------------------------------------------------------------------------
num_robots_strategy = st.integers(min_value=2, max_value=20)
valid_position_strategy = st.sampled_from(list_of_open_cells)
battery_pct_strategy = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
urgency_strategy = st.integers(min_value=1, max_value=5)


@st.composite
def random_scenario_strategy(draw: Any) -> Dict[str, Any]:
    """
    Builds a full random scenario:
      - N robots (between 2 and 20)
      - Each robot has a random valid start position and a distinct valid goal position.
      - Goals must not equal each other or any robot's start position.
      - Each robot has a random battery level (0.0 to 100.0) and task urgency (1 to 5).
    """
    n = draw(num_robots_strategy)
    # Select 2 * n distinct open cells:
    # First n are start positions, remaining n are goal positions.
    # This mathematically guarantees all starts are unique, all goals are unique,
    # and no goal equals any robot's start position.
    chosen_positions = draw(st.lists(
        valid_position_strategy,
        min_size=2 * n,
        max_size=2 * n,
        unique=True,
    ))
    starts = chosen_positions[:n]
    goals = chosen_positions[n:]

    robots_spec = []
    for i in range(n):
        batt = draw(battery_pct_strategy)
        urg = draw(urgency_strategy)
        robots_spec.append({
            "robot_id": f"AMR-{i+1:02d}",
            "start": starts[i],
            "goal": goals[i],
            "battery_pct": round(batt, 1),
            "urgency": urg,
        })

    return {
        "num_robots": n,
        "robots": robots_spec,
    }


# ---------------------------------------------------------------------------
# Failure Formatter
# ---------------------------------------------------------------------------
def format_failure_dump(
    scenario: Dict[str, Any],
    tick: int,
    error_msg: str,
    robots: Dict[str, Robot],
    tasks: Dict[str, Task],
    conflict_robots: List[str] | None = None,
) -> str:
    """Format full scenario and state dump for instant reproducibility."""
    lines = [
        "=" * 80,
        f"PROPERTY-BASED FUZZ TEST FAILURE AT TICK {tick}",
        f"ERROR: {error_msg}",
        "-" * 80,
        "SCENARIO CONFIGURATION:",
        f"  Total AMRs: {scenario['num_robots']}",
    ]
    for spec in scenario["robots"]:
        lines.append(
            f"  {spec['robot_id']}: Start={spec['start']} -> Goal={spec['goal']} | "
            f"Battery={spec['battery_pct']}% | Urgency={spec['urgency']}"
        )
    lines.append("-" * 80)
    if conflict_robots:
        lines.append("CONFLICTING / INVOLVED ROBOTS STATE:")
        for rid in conflict_robots:
            r = robots.get(rid)
            if r:
                lines.append(
                    f"  {r.robot_id}: Pos={r.position}, Heading={r.heading}, State={r.state}, "
                    f"Battery={r.battery_pct}%, Priority={r.priority_score:.1f}, Waits={r.wait_ticks_so_far}, "
                    f"PathHead={r.path[:3] if r.path else '[]'}"
                )
    lines.append("-" * 80)
    lines.append("ALL ROBOTS CURRENT STATE:")
    for rid, r in robots.items():
        t = tasks.get(r.current_task_id) if r.current_task_id else None
        t_status = t.status if t else "NONE"
        lines.append(
            f"  {r.robot_id}: Pos={r.position}, State={r.state}, TaskStatus={t_status}, "
            f"Waits={r.wait_ticks_so_far}, PathLen={len(r.path)}"
        )
    lines.append("=" * 80)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Real Simulation Runner
# ---------------------------------------------------------------------------
def run_real_simulation_scenario(
    scenario: Dict[str, Any],
    max_ticks: int,
    assert_no_starvation: bool = False,
) -> Dict[str, Any]:
    """
    Executes the REAL simulation tick loop end-to-end:
      - Uses real WarehouseGrid with real warehouse obstacles
      - Calls real find_path, detect_conflicts, resolve_conflict, run_conflict_engine_tick
      - Enforces real reservation table and priority recalculations
    """
    grid = WAREHOUSE_GRID
    robots: Dict[str, Robot] = {}
    tasks: Dict[str, Task] = {}
    reservation_table: Dict[Tuple[int, int, int], str] = {}

    HOLD = 30

    # 1. Instantiate real Task and Robot data structures
    for spec in scenario["robots"]:
        rid = spec["robot_id"]
        tid = f"TASK-{rid}"
        task = Task(
            task_id=tid,
            pickup=spec["start"],
            dropoff=spec["goal"],
            urgency=spec["urgency"],
            created_tick=0,
            assigned_robot_id=rid,
            status="IN_PROGRESS",  # Directly en route to destination goal
        )
        robot = Robot(
            robot_id=rid,
            position=spec["start"],
            heading=Heading.NORTH,
            state=RobotState.EN_ROUTE,
            battery_pct=spec["battery_pct"],
            current_task_id=tid,
            path=[],
            priority_score=0.0,
            wait_ticks_so_far=0,
            last_updated_tick=0,
        )
        robots[rid] = robot
        tasks[tid] = task

    # 2. Pre-reserve initial positions at tick 0
    for r in robots.values():
        reserve_path([{"x": r.position[0], "y": r.position[1], "t": 0}], r.robot_id, reservation_table, hold_ticks_at_goal=HOLD)

    # 3. Initial Path Planning in priority order
    for rid, robot in sorted(robots.items(), key=lambda item: (-tasks[item[1].current_task_id].urgency, item[0])):
        task = tasks[robot.current_task_id]
        dist = robot.distance_to_goal()
        robot.priority_score = calculate_priority_score(robot, task, dist)

        release_reservations(robot.robot_id, reservation_table)
        path = find_path(
            start=robot.position,
            goal=task.dropoff,
            current_tick=0,
            reservation_table=reservation_table,
            robot_id=robot.robot_id,
            grid=grid,
        )
        if path:
            robot.path = path
            reserve_path(path, robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)
        else:
            robot.path = [{"x": robot.position[0], "y": robot.position[1], "t": 0}]
            reserve_path(robot.path, robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)

    # Pathfinding callback for conflict replanning
    def _pathfinder_fn(start, goal, cur_tick, res_table, robot_id=None, **kwargs):
        return find_path(
            start=start,
            goal=goal,
            current_tick=cur_tick,
            reservation_table=res_table,
            robot_id=robot_id,
            grid=grid,
        )

    # 4. Tick loop
    for tick in range(max_ticks):
        # Stop early if all robots have completed their missions
        if all(r.state == RobotState.IDLE for r in robots.values()):
            break

        prev_positions = {r.robot_id: r.position for r in robots.values()}

        # A. Maintain continuous reservation for idle/parked robots
        for robot in robots.values():
            if robot.state == RobotState.IDLE or not robot.path or len(robot.path) <= 1:
                for dt in range(HOLD):
                    reservation_table[(robot.position[0], robot.position[1], tick + dt)] = robot.robot_id

        # B. Retry route planning for any EN_ROUTE robot waiting or needing a path
        for robot in robots.values():
            if robot.state == RobotState.EN_ROUTE and (not robot.path or len(robot.path) <= 1 or robot.wait_ticks_so_far > 0):
                task = tasks[robot.current_task_id]
                target = task.pickup if task.status == "ASSIGNED" else task.dropoff
                release_reservations(robot.robot_id, reservation_table)
                re_path = find_path(robot.position, target, tick, reservation_table, robot_id=robot.robot_id, grid=grid)
                if re_path and len(re_path) > 1:
                    robot.path = re_path
                    reserve_path(re_path, robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)
                else:
                    robot.path = [{"x": robot.position[0], "y": robot.position[1], "t": tick}]
                    reserve_path(robot.path, robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)

        # C. Run Conflict Engine (Member 3)
        conflict_res = run_conflict_engine_tick(
            robots=robots,
            tasks=tasks,
            reservation_table=reservation_table,
            current_tick=tick,
            find_path_fn=_pathfinder_fn,
        )

        # Re-reserve loser's path if adjusted
        for resolution in conflict_res["resolutions"]:
            loser_id = resolution["loser_id"]
            loser_robot = robots[loser_id]
            if loser_robot.path:
                reserve_path(loser_robot.path, loser_robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)

        # D. Advance Robots along their assigned paths
        for robot in robots.values():
            if robot.state == RobotState.IDLE or not robot.path:
                continue

            if len(robot.path) > 1 and robot.path[1]["t"] == tick + 1:
                next_step = robot.path[1]
                next_pos = (next_step["x"], next_step["y"])

                # Update heading
                dx = next_pos[0] - robot.position[0]
                dy = next_pos[1] - robot.position[1]
                if dx > 0: robot.heading = Heading.EAST
                elif dx < 0: robot.heading = Heading.WEST
                elif dy > 0: robot.heading = Heading.SOUTH
                elif dy < 0: robot.heading = Heading.NORTH

                robot.position = next_pos
                robot.path = robot.path[1:]
                robot.wait_ticks_so_far = 0
            else:
                robot.wait_ticks_so_far += 1

            robot.last_updated_tick = tick

            # Handle Task Completion at goal
            task = tasks[robot.current_task_id]
            if robot.position == task.dropoff:
                task.status = "COMPLETED"
                robot.state = RobotState.IDLE
                release_reservations(robot.robot_id, reservation_table)
                robot.path = []
                for dt in range(HOLD):
                    reservation_table[(robot.position[0], robot.position[1], tick + dt)] = robot.robot_id

        # E. Prune past reservations
        prune_past(reservation_table, tick)

        # =====================================================================
        # ASSERTION 1: No two robots occupy the same (x, y) at the same time
        # =====================================================================
        all_positions = [r.position for r in robots.values()]
        if len(all_positions) != len(set(all_positions)):
            # Find colliding robots
            colliding_pos = [pos for pos in all_positions if all_positions.count(pos) > 1][0]
            colliding_ids = [r.robot_id for r in robots.values() if r.position == colliding_pos]
            dump = format_failure_dump(
                scenario,
                tick,
                f"CELL COLLISION! AMRs {colliding_ids} share cell {colliding_pos} at tick {tick}",
                robots,
                tasks,
                colliding_ids,
            )
            print(dump, flush=True)
            raise AssertionError(dump)

        # =====================================================================
        # ASSERTION 2: No swap conflict occurred
        # =====================================================================
        all_ids = list(robots.keys())
        for i, id_a in enumerate(all_ids):
            for id_b in all_ids[i + 1:]:
                p_a_prev, p_a_now = prev_positions[id_a], robots[id_a].position
                p_b_prev, p_b_now = prev_positions[id_b], robots[id_b].position
                is_swap = (p_a_prev == p_b_now and p_b_prev == p_a_now and p_a_prev != p_b_prev)
                if is_swap:
                    dump = format_failure_dump(
                        scenario,
                        tick,
                        f"HEAD-ON SWAP CONFLICT between {id_a} ({p_a_prev}->{p_a_now}) and {id_b} ({p_b_prev}->{p_b_now}) at tick {tick}",
                        robots,
                        tasks,
                        [id_a, id_b],
                    )
                    print(dump, flush=True)
                    raise AssertionError(dump)

    # =========================================================================
    # ASSERTION 3: No Starvation (Test 2 Horizon Check)
    # =========================================================================
    if assert_no_starvation:
        active_unfinished = [
            r for r in robots.values()
            if r.state in (RobotState.EN_ROUTE, RobotState.CONFLICT_NEGOTIATING)
        ]
        if active_unfinished:
            # Check if any active robot is stuck waiting while lower-priority robots progressed
            stuck_robots = [r for r in active_unfinished if r.wait_ticks_so_far > 35]
            if stuck_robots:
                stuck_ids = [r.robot_id for r in stuck_robots]
                dump = format_failure_dump(
                    scenario,
                    max_ticks,
                    f"STARVATION DETECTED! AMRs {stuck_ids} remained stuck after {max_ticks} ticks. "
                    f"Waits={[r.wait_ticks_so_far for r in stuck_robots]}, States={[r.state.value for r in stuck_robots]}",
                    robots,
                    tasks,
                    stuck_ids,
                )
                print(dump, flush=True)
                raise AssertionError(dump)
            elif active_unfinished:
                # Still en route but hasn't completed within window
                unfinished_ids = [r.robot_id for r in active_unfinished]
                dump = format_failure_dump(
                    scenario,
                    max_ticks,
                    f"STARVATION / COMPLETION TIMEOUT! AMRs {unfinished_ids} failed to reach goal within {max_ticks} ticks.",
                    robots,
                    tasks,
                    unfinished_ids,
                )
                print(dump, flush=True)
                raise AssertionError(dump)

    return {"completed": True, "robots": robots}


# ---------------------------------------------------------------------------
# Live Progress Reporting for Terminal Feedback
# ---------------------------------------------------------------------------
_test_1_stats: Dict[str, Any] = {"count": 0, "start": 0.0}
_test_2_stats: Dict[str, Any] = {"count": 0, "start": 0.0}


def _report_live_progress(test_num: int, name: str, stats: Dict[str, Any], total: int = 500) -> None:
    """Displays a live updating progress bar directly in the terminal."""
    if stats["count"] == 0:
        stats["start"] = time.time()
    stats["count"] += 1
    c = stats["count"]
    if c == 1 or c % 20 == 0 or c == total:
        elapsed = time.time() - stats["start"]
        avg = elapsed / c
        pct = (c / total) * 100.0
        bar_len = 24
        filled = int(bar_len * c / total)
        bar = "=" * filled + "-" * (bar_len - filled)
        line = f"\r[Test {test_num}/2: {name}] [{bar}] {c}/{total} ({pct:3.0f}%) | Elapsed: {elapsed:5.1f}s | Avg: {avg:.3f}s/ex"
        # Write to both __stderr__ (bypasses pytest capture) and stderr for reliability
        out = getattr(sys, "__stderr__", sys.stderr) or sys.stderr
        try:
            out.write(line)
            out.flush()
        except Exception:
            pass
        if c == total:
            try:
                out.write("\n")
                out.flush()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 1 — No-collision invariant
# ---------------------------------------------------------------------------
@given(scenario=random_scenario_strategy())
@settings(max_examples=500, deadline=None)
def test_no_two_robots_ever_share_a_cell(scenario: Dict[str, Any]) -> None:
    """
    Runs the REAL simulation tick loop for up to 200 ticks on this randomly
    generated scenario.

    After EVERY tick, asserts:
      - No two robots occupy the same (x, y) at the same time
      - No swap conflict occurred (robot A and B did not exchange positions
        in a single tick)
    """
    _report_live_progress(1, "No-Collision Invariant", _test_1_stats, total=500)
    run_real_simulation_scenario(scenario=scenario, max_ticks=200, assert_no_starvation=False)


# ---------------------------------------------------------------------------
# Test 2 — No-starvation invariant
# ---------------------------------------------------------------------------
@given(scenario=random_scenario_strategy())
@settings(max_examples=500, deadline=None)
def test_no_robot_starves(scenario: Dict[str, Any]) -> None:
    """
    Runs the REAL simulation tick loop for up to 500 ticks.

    Tracks every robot's wait_ticks_so_far across the run.
    Asserts every robot reaches its goal and transitions to COMPLETED / IDLE
    without being starved by lower-priority robots.
    """
    _report_live_progress(2, "No-Starvation Invariant", _test_2_stats, total=500)
    run_real_simulation_scenario(scenario=scenario, max_ticks=500, assert_no_starvation=True)


if __name__ == "__main__":
    print("\nStarting Property-Based Fuzz Testing Suite (Hypothesis, 500 scenarios each)...", flush=True)
    sys.exit(pytest.main(["-v", "-s", __file__]))

