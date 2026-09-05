"""
full_integration_test.py
Automated End-to-End Test Harness for SIH-2026 Fleet Coordination.

Directly imports:
  - Member 2: pathfinding.grid.WarehouseGrid, pathfinding.pathfinder.find_path, pathfinding.reservations.*
  - Member 3: conflict_engine.conflict_engine.run_conflict_engine_tick, conflict_detector.*, arbitration.*, priority.*
  - Data models: models.Robot, models.Task, models.Heading, models.RobotState

Runs 50 randomized scenarios with varying fleet sizes (N in [3, 5, 10, 20]),
asserting strict physical and algorithmic invariants on every single tick.
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

# Set up imports
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner, find_path
from reservations import reserve_path, release_reservations, prune_past
from priority import calculate_priority_score
from conflict_detector import detect_conflicts
from arbitration import resolve_conflict
from conflict_engine import run_conflict_engine_tick
from models import Heading, Robot, RobotState, Task


def get_static_shelves() -> list[tuple[int, int]]:
    obs = []
    for y in (5, 10, 15, 20):
        for x in range(3, 27):
            if x not in (7, 14, 21):
                obs.append((x, y))
    return obs


def create_random_scenario(seed: int, num_robots: int, grid: WarehouseGrid) -> tuple[dict[str, Robot], dict[str, Task]]:
    rng = random.Random(seed)
    width, height = grid.width, grid.height
    
    free_cells = [
        (x, y)
        for x in range(width)
        for y in range(height)
        if grid.is_free((x, y))
    ]
    rng.shuffle(free_cells)

    robots = {}
    tasks = {}

    for i in range(num_robots):
        rid = f"AMR-{i+1:02d}"
        tid = f"TASK-{i+1:02d}"
        start_pos = free_cells[i]
        pickup_pos = free_cells[num_robots + i]
        dropoff_pos = free_cells[2 * num_robots + i]
        urgency = rng.randint(1, 5)

        task = Task(
            task_id=tid,
            pickup=pickup_pos,
            dropoff=dropoff_pos,
            urgency=urgency,
            created_tick=0,
            assigned_robot_id=rid,
            status="ASSIGNED",
        )
        tasks[tid] = task

        robot = Robot(
            robot_id=rid,
            position=start_pos,
            heading=Heading.NORTH,
            state=RobotState.EN_ROUTE,
            battery_pct=round(rng.uniform(60.0, 100.0), 1),
            current_task_id=tid,
            path=[],
            priority_score=0.0,
            wait_ticks_so_far=0,
            last_updated_tick=0,
        )
        robots[rid] = robot

    return robots, tasks


def run_scenario(seed: int, num_robots: int, max_ticks: int = 100, record_frames: bool = False) -> dict:
    obstacles = get_static_shelves()
    grid = WarehouseGrid(obstacles=obstacles, width=30, height=30)
    robots, tasks = create_random_scenario(seed, num_robots, grid)
    reservation_table: dict[tuple[int, int, int], str] = {}

    STARVATION_THRESHOLD = 35  # Maximum allowable consecutive wait ticks

    HOLD = 30

    # Step 0: Pre-reserve initial positions for all robots so higher-priority planners don't collide with parked robots
    for r in robots.values():
        reserve_path([{"x": r.position[0], "y": r.position[1], "t": 0}], r.robot_id, reservation_table, hold_ticks_at_goal=HOLD)

    # Step 1: Initial Path Planning for all robots in priority order
    for rid, robot in sorted(robots.items(), key=lambda item: (-tasks[item[1].current_task_id].urgency, item[0])):
        task = tasks[robot.current_task_id]
        dist = robot.distance_to_goal()
        robot.priority_score = calculate_priority_score(robot, task, dist)
        
        release_reservations(robot.robot_id, reservation_table)
        path = find_path(
            start=robot.position,
            goal=task.pickup,
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

    recorded_history = []
    total_conflicts_resolved = 0

    # Step 2: Tick Loop
    for tick in range(max_ticks):
        prev_positions = {r.robot_id: r.position for r in robots.values()}
        prev_paths = {r.robot_id: list(r.path) for r in robots.values()}

        # 1. Pathfinding callback for conflict replanning
        def _pathfinder_fn(start, goal, cur_tick, res_table, robot_id=None, **kwargs):
            return find_path(
                start=start,
                goal=goal,
                current_tick=cur_tick,
                reservation_table=res_table,
                robot_id=robot_id,
                grid=grid,
            )

        # 2. Maintain continuous reservation for idle/parked robots so moving robots cannot plan into or pass over them
        for robot in robots.values():
            if robot.state == RobotState.IDLE or not robot.path or len(robot.path) <= 1:
                for dt in range(HOLD):
                    reservation_table[(robot.position[0], robot.position[1], tick + dt)] = robot.robot_id

        # 3. Retry route planning for any EN_ROUTE robot that was waiting due to a temporary block
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

        # 4. Run Conflict Engine (Member 3)
        conflict_res = run_conflict_engine_tick(
            robots=robots,
            tasks=tasks,
            reservation_table=reservation_table,
            current_tick=tick,
            find_path_fn=_pathfinder_fn,
        )
        total_conflicts_resolved += conflict_res["conflicts_found"]

        # Invariant 4 Check: If a robot lost an arbitration, its path or state must have adjusted
        for resolution in conflict_res["resolutions"]:
            loser_id = resolution["loser_id"]
            loser_robot = robots[loser_id]
            # Ensure loser wait ticks incremented
            assert loser_robot.wait_ticks_so_far > 0, (
                f"[Seed {seed} Tick {tick}] Loser {loser_id} wait_ticks_so_far was not incremented!"
            )
            # Re-reserve loser's new path
            if loser_robot.path:
                reserve_path(loser_robot.path, loser_robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)

        # 5. Advance Robots along their assigned paths
        for robot in robots.values():
            if robot.state == RobotState.IDLE or not robot.path:
                continue

            # Find next position along path
            # If path has waypoints for tick+1, advance to next step
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
                # Robot stayed in place (waited or turned)
                robot.wait_ticks_so_far += 1

            robot.last_updated_tick = tick

            # Handle Task Transitions (Pickup -> Dropoff -> Done)
            task = tasks[robot.current_task_id]
            if robot.position == task.pickup and task.status == "ASSIGNED":
                task.status = "IN_PROGRESS"
                # Replan to dropoff starting from tick + 1 (robot just arrived at tick + 1)
                release_reservations(robot.robot_id, reservation_table)
                new_path = find_path(robot.position, task.dropoff, tick + 1, reservation_table, robot_id=robot.robot_id, grid=grid)
                if new_path:
                    robot.path = new_path
                    reserve_path(new_path, robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)
                else:
                    # Hold in place at pickup until route clears
                    robot.path = [
                        {"x": robot.position[0], "y": robot.position[1], "t": tick + 1},
                        {"x": robot.position[0], "y": robot.position[1], "t": tick + 2},
                    ]
                    reserve_path(robot.path, robot.robot_id, reservation_table, hold_ticks_at_goal=HOLD)
            elif robot.position == task.dropoff and task.status == "IN_PROGRESS":
                task.status = "COMPLETED"
                robot.state = RobotState.IDLE
                release_reservations(robot.robot_id, reservation_table)
                robot.path = []
                for dt in range(HOLD):
                    reservation_table[(robot.position[0], robot.position[1], tick + dt)] = robot.robot_id

        # Prune old reservations
        prune_past(reservation_table, tick)

        # =========================================================================
        # STEP 3: STRICT INVARIANT ASSERTIONS (Run every single tick)
        # =========================================================================
        # Invariant 1: No two robots ever occupy the same cell at the same tick (including IDLE robots)
        all_positions = [r.position for r in robots.values()]
        assert len(all_positions) == len(set(all_positions)), (
            f"[FAIL: Seed {seed} Tick {tick}] Collision! Multiple robots share a cell: "
            f"{[pos for pos in all_positions if all_positions.count(pos) > 1]}"
        )

        # Invariant 2: No head-on swap conflict occurred between any pair of robots
        all_ids = list(robots.keys())
        for i, id_a in enumerate(all_ids):
            for id_b in all_ids[i + 1:]:
                p_a_prev, p_a_now = prev_positions[id_a], robots[id_a].position
                p_b_prev, p_b_now = prev_positions[id_b], robots[id_b].position
                is_swap = (p_a_prev == p_b_now and p_b_prev == p_a_now and p_a_prev != p_b_prev)
                assert not is_swap, (
                    f"[FAIL: Seed {seed} Tick {tick}] Head-on SWAP between {id_a} and {id_b}! "
                    f"{id_a}: {p_a_prev}->{p_a_now}, {id_b}: {p_b_prev}->{p_b_now}"
                )

        # Invariant 3: Position matches current path for active robots
        active_robots = [r for r in robots.values() if r.state != RobotState.IDLE]
        for robot in active_robots:
            if robot.path:
                first_node = robot.path[0]
                expected_pos = (first_node["x"], first_node["y"])
                assert robot.position == expected_pos, (
                    f"[FAIL: Seed {seed} Tick {tick}] {robot.robot_id} position {robot.position} "
                    f"does not match path head {expected_pos}!"
                )

        # Invariant 5: Starvation check (no robot waits for > STARVATION_THRESHOLD ticks consecutively)
        for robot in active_robots:
            assert robot.wait_ticks_so_far <= STARVATION_THRESHOLD, (
                f"[FAIL: Seed {seed} Tick {tick}] Starvation detected on {robot.robot_id}! "
                f"Waited {robot.wait_ticks_so_far} ticks consecutively at {robot.position}."
            )

        if record_frames:
            recorded_history.append({
                "tick": tick,
                "robots": [
                    {
                        "id": r.robot_id,
                        "x": r.position[0],
                        "y": r.position[1],
                        "heading": r.heading.value if hasattr(r.heading, "value") else str(r.heading),
                        "state": r.state.value if hasattr(r.state, "value") else str(r.state),
                        "carrying": tasks[r.current_task_id].status == "IN_PROGRESS" if r.current_task_id else False,
                        "completed": tasks[r.current_task_id].status == "COMPLETED" if r.current_task_id else False,
                        "battery": r.battery_pct,
                        "priority": r.priority_score,
                        "waits": r.wait_ticks_so_far,
                        "path": r.path[:6],
                        "pickup": list(tasks[r.current_task_id].pickup),
                        "drop": list(tasks[r.current_task_id].dropoff),
                    }
                    for r in robots.values()
                ],
                "conflicts": conflict_res["resolutions"],
            })

    return {
        "seed": seed,
        "num_robots": num_robots,
        "completed": True,
        "conflicts_resolved": total_conflicts_resolved,
        "frames": recorded_history,
    }


def main():
    print("=" * 80)
    print("RUNNING 50 RANDOMIZED FULL INTEGRATION SCENARIOS")
    print("Directly calling real find_path, detect_conflicts, resolve_conflict, and run_conflict_engine_tick")
    print("=" * 80)

    fleet_sizes = [3, 5, 10, 20]
    total_scenarios = 50
    passed = 0
    t_start = time.perf_counter()

    for scenario_idx in range(1, total_scenarios + 1):
        seed = 1000 + scenario_idx
        num_robots = fleet_sizes[(scenario_idx - 1) % len(fleet_sizes)]
        print(f"Scenario {scenario_idx:02d}/50: Seed={seed}, Robots={num_robots:02d} ... ", end="", flush=True)

        try:
            res = run_scenario(seed=seed, num_robots=num_robots, max_ticks=100, record_frames=(scenario_idx == 50))
            passed += 1
            print(f"PASSED (Ticks=100, Conflicts={res['conflicts_resolved']})")
        except AssertionError as err:
            print(f"\nFAILED with assertion error:\n{err}")
            sys.exit(1)
        except Exception as exc:
            print(f"\nCRASHED with unexpected exception:\n{exc}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    elapsed = time.perf_counter() - t_start
    print("=" * 80)
    print(f"ALL {passed}/{total_scenarios} RANDOMIZED SCENARIOS PASSED CLEANLY IN {elapsed:.2f}s!")
    print("ZERO Collisions, ZERO Swaps, ZERO Path Misalignments, ZERO Starvations.")
    print("=" * 80)


if __name__ == "__main__":
    main()
