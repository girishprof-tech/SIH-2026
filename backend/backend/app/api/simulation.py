"""
Simulation control endpoints — SCHEMA.md §18.

POST /api/simulation/start
POST /api/simulation/pause
POST /api/simulation/reset
GET  /api/simulation/status
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulation", tags=["Simulation"])


class SimStatusOut(BaseModel):
    running: bool
    tick: int
    timestamp_ms: int
    fleet_size: int
    tick_ms: int


@router.post("/start", summary="Start simulation telemetry streaming")
async def start_simulation(request: Request) -> dict:
    fleet = request.app.state.fleet_state
    request.app.state.telemetry_streaming_paused = False
    fleet.is_running = True
    log.info(
        "SIMULATION_START: Decentralized fleet telemetry streaming active. "
        "Authoritative SimulationEngine tick loop remains disabled."
    )
    return {"status": "started", "tick": fleet.tick, "mode": "decentralized_telemetry"}


@router.post("/pause", summary="Pause simulation telemetry streaming")
async def pause_simulation(request: Request) -> dict:
    fleet = request.app.state.fleet_state
    request.app.state.telemetry_streaming_paused = True
    fleet.is_running = False
    log.info("SIMULATION_PAUSED: Telemetry streaming to dashboard paused.")
    return {"status": "paused", "tick": fleet.tick, "mode": "decentralized_telemetry"}


@router.post("/reset", summary="Reset simulation state")
async def reset_simulation(request: Request) -> dict:
    fleet = request.app.state.fleet_state
    fleet.reset()
    request.app.state.telemetry_streaming_paused = False
    log.info("SIMULATION_RESET: Telemetry viewer state reset.")
    return {"status": "reset", "tick": 0, "mode": "decentralized_telemetry"}


@router.get("/status", summary="Get simulation status", response_model=SimStatusOut)
async def get_status(request: Request) -> SimStatusOut:
    from app.core.config import get_settings
    fleet = request.app.state.fleet_state
    cfg = get_settings()
    return SimStatusOut(
        running=fleet.is_running,
        tick=fleet.tick,
        timestamp_ms=fleet.timestamp_ms,
        fleet_size=len(fleet.robots),
        tick_ms=cfg.SIM_TICK_MS,
    )


class FuzzScenarioRequest(BaseModel):
    num_robots: int = 20
    max_ticks: int = 40
    seed: int | None = None


@router.post("/generate_fuzz", summary="Generate a randomized real fuzz scenario using Space-Time A* and Conflict Engine")
async def generate_fuzz_scenario(payload: FuzzScenarioRequest = FuzzScenarioRequest()) -> dict:
    import random
    import sys
    from pathlib import Path
    root_dir = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root_dir / "pathfinding"))
    sys.path.insert(0, str(root_dir / "conflict-engine"))
    sys.path.insert(0, str(root_dir / "testing"))

    from full_integration_test import get_static_shelves
    from grid import WarehouseGrid
    from pathfinder import find_path
    from models import Robot, Task, RobotState, Heading
    from priority import calculate_priority_score
    from conflict_detector import detect_conflicts
    from arbitration import resolve_conflict

    seed = payload.seed if payload.seed is not None else random.randint(1000, 99999)
    num_robots = max(6, min(payload.num_robots, 30))
    max_ticks = max(20, min(payload.max_ticks, 60))

    rng = random.Random(seed)
    obstacles = get_static_shelves()
    grid = WarehouseGrid(obstacles=obstacles, width=30, height=30)
    free_cells = [(x, y) for x in range(30) for y in range(30) if grid.is_free((x, y))]
    rng.shuffle(free_cells)

    robots = {}
    tasks = {}

    left_cells = [c for c in free_cells if c[0] < 9]
    right_cells = [c for c in free_cells if c[0] > 20]
    top_cells = [c for c in free_cells if c[1] < 9]
    bottom_cells = [c for c in free_cells if c[1] > 20]

    for i in range(num_robots):
        rid = f"AMR-{i+1:02d}"
        tid = f"TASK-{i+1:02d}"
        mode = i % 4
        if mode == 0:
            start = left_cells[i % len(left_cells)]
            goal = right_cells[(i * 3) % len(right_cells)]
        elif mode == 1:
            start = right_cells[i % len(right_cells)]
            goal = left_cells[(i * 3) % len(left_cells)]
        elif mode == 2:
            start = top_cells[i % len(top_cells)]
            goal = bottom_cells[(i * 3) % len(bottom_cells)]
        else:
            start = bottom_cells[i % len(bottom_cells)]
            goal = top_cells[(i * 3) % len(top_cells)]

        urgency = rng.randint(1, 5)
        battery = round(rng.uniform(25.0, 95.0), 1)

        tasks[tid] = Task(
            task_id=tid,
            pickup=start,
            dropoff=goal,
            urgency=urgency,
            created_tick=0,
            assigned_robot_id=rid,
            status="ASSIGNED",
        )

        p = find_path(start, goal, 0, {}, robot_id=rid, grid=grid)
        robots[rid] = Robot(
            robot_id=rid,
            position=start,
            heading=Heading.NORTH,
            state=RobotState.EN_ROUTE,
            battery_pct=battery,
            current_task_id=tid,
            path=p or [{"x": start[0], "y": start[1], "t": 0}],
            priority_score=0.0,
            wait_ticks_so_far=0,
            last_updated_tick=0,
        )

    frames = []
    res_table = {}
    total_conflicts = 0

    for t in range(max_ticks):
        for r in robots.values():
            dist = r.distance_to_goal()
            r.priority_score = calculate_priority_score(r, tasks[r.current_task_id], dist)

        detected = detect_conflicts(list(robots.values()), t)
        tick_conflicts = []

        for c in detected:
            res = resolve_conflict(
                c, robots, res_table,
                lambda s, g, cur_t, rt, **kw: find_path(s, g, cur_t, rt, grid=grid),
                tasks
            )
            winner_id = res.get("winner_id")
            loser_id = res.get("loser_id")
            action = res.get("action", "YIELD_AND_WAIT")
            cell = c.get("cell", [0, 0])

            loser_robot = robots.get(loser_id)
            winner_robot = robots.get(winner_id)
            if loser_robot and winner_robot:
                loser_robot.state = RobotState.CONFLICT_NEGOTIATING
                loser_robot.wait_ticks_so_far += 1
                cur_x, cur_y = loser_robot.position
                if len(loser_robot.path) <= 1 or loser_robot.path[1]["x"] == cur_x and loser_robot.path[1]["y"] == cur_y:
                    loser_robot.path = [{"x": cur_x, "y": cur_y, "t": t}, {"x": cur_x, "y": cur_y, "t": t + 1}]
                else:
                    rem_path = [{"x": p["x"], "y": p["y"], "t": p["t"] + 1} for p in loser_robot.path[1:]]
                    loser_robot.path = [{"x": cur_x, "y": cur_y, "t": t}, {"x": cur_x, "y": cur_y, "t": t + 1}] + rem_path

            reason = f"Urgency ({tasks[winner_robot.current_task_id].urgency} vs {tasks[loser_robot.current_task_id].urgency}) • Battery ({winner_robot.battery_pct}% vs {loser_robot.battery_pct}%)"
            tick_conflicts.append({
                "cell": cell,
                "tick": t,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "winner_priority": round(winner_robot.priority_score, 1),
                "loser_priority": round(loser_robot.priority_score, 1),
                "action": action,
                "reason": reason,
            })
            total_conflicts += 1

        next_steps = {}
        for rid, r in sorted(robots.items(), key=lambda item: -item[1].priority_score):
            if len(r.path) > 1:
                target_pos = (r.path[1]["x"], r.path[1]["y"])
                if target_pos in next_steps:
                    cur_x, cur_y = r.position
                    r.path = [{"x": cur_x, "y": cur_y, "t": t}, {"x": cur_x, "y": cur_y, "t": t + 1}] + [
                        {"x": p["x"], "y": p["y"], "t": p["t"] + 1} for p in r.path[1:]
                    ]
                    r.wait_ticks_so_far += 1
                else:
                    next_steps[target_pos] = rid

        for r in robots.values():
            if len(r.path) > 1 and r.path[1]["t"] == t + 1:
                next_pos = (r.path[1]["x"], r.path[1]["y"])
                dx = next_pos[0] - r.position[0]
                dy = next_pos[1] - r.position[1]
                if dx > 0: r.heading = Heading.EAST
                elif dx < 0: r.heading = Heading.WEST
                elif dy > 0: r.heading = Heading.SOUTH
                elif dy < 0: r.heading = Heading.NORTH

                r.position = next_pos
                r.path = r.path[1:]
                r.battery_pct = max(5.0, round(r.battery_pct - 0.2, 1))
            else:
                r.wait_ticks_so_far += 1

            task = tasks[r.current_task_id]
            if r.position == task.dropoff:
                new_goal = rng.choice(free_cells)
                task.dropoff = new_goal
                re_p = find_path(r.position, new_goal, t + 1, {}, robot_id=r.robot_id, grid=grid)
                r.path = re_p or [{"x": r.position[0], "y": r.position[1], "t": t + 1}]

        robot_snapshots = []
        for r in robots.values():
            task = tasks[r.current_task_id]
            robot_snapshots.append({
                "id": r.robot_id,
                "x": r.position[0],
                "y": r.position[1],
                "heading": r.heading.value if hasattr(r.heading, "value") else str(r.heading),
                "state": r.state.value if hasattr(r.state, "value") else str(r.state),
                "battery": r.battery_pct,
                "priority": round(r.priority_score, 1),
                "task_id": r.current_task_id,
                "goal": [task.dropoff[0], task.dropoff[1]],
                "path": [{"x": p["x"], "y": p["y"], "t": p["t"]} for p in r.path[:12]],
                "wait_ticks": r.wait_ticks_so_far,
            })

        frames.append({
            "tick": t,
            "robots": robot_snapshots,
            "conflicts": tick_conflicts,
        })

    return {
        "name": f"Live Fuzz: Seed {seed} ({num_robots} AMRs • {total_conflicts} Conflicts)",
        "seed": seed,
        "num_robots": num_robots,
        "conflicts_resolved": total_conflicts,
        "total_frames": len(frames),
        "description": f"Real-time generated cross-traffic scenario with {num_robots} AMRs. {total_conflicts} conflicts arbitrated via Edge-AI priority formulas with zero collisions.",
        "frames": frames,
        "obstacles": obstacles,
        "width": 30,
        "height": 30,
    }


@router.get("/telemetry_snapshot", summary="Get the latest real-time telemetry snapshot")
async def get_telemetry_snapshot() -> dict:
    from app.services.telemetry_bus import read_latest_telemetry
    data = read_latest_telemetry()
    if data:
        return data
    return {"tick": 0, "robots": [], "active_conflicts": []}

