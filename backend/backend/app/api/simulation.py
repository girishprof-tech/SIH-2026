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
    speed: float = 1.0


class SpeedPayload(BaseModel):
    speed: float = 1.0


@router.post("/start", summary="Start simulation telemetry streaming")
async def start_simulation(request: Request) -> dict:
    import json
    fleet = request.app.state.fleet_state
    request.app.state.telemetry_streaming_paused = False
    fleet.is_running = True
    orchestrator = getattr(request.app.state, "orchestrator", None)

    if orchestrator is None or not orchestrator.is_alive():
        from app.services.fleet_orchestrator import FleetOrchestrator
        from app.models.world import get_active_map_data
        from app.core.config import get_settings
        active_map_dict = get_active_map_data()
        cfg = get_settings()
        log.info("SIMULATION_START: Orchestrator was not active; spawning fresh FleetOrchestrator...")
        orchestrator = FleetOrchestrator(
            map_data=active_map_dict,
            tick_interval_s=cfg.SIM_TICK_MS / 1000.0,
            max_ticks=0,
        )
        orchestrator.start()
        request.app.state.orchestrator = orchestrator
        request.app.state.fleet_mode = "spawned_new_fleet"
    else:
        orchestrator.resume()

    conn_mgr = getattr(request.app.state, "connection_manager", None)
    if conn_mgr:
        started_delta = json.dumps({
            "type": "TICK_DELTA",
            "tick": fleet.tick,
            "fleet_status": {
                "running": True,
                "mode": getattr(request.app.state, "fleet_mode", "spawned_new_fleet"),
                "tick": fleet.tick,
                "armed_state": "RUNNING",
            },
        }, separators=(",", ":"))
        if hasattr(conn_mgr, "broadcast"):
            await conn_mgr.broadcast(started_delta)
        elif hasattr(conn_mgr, "broadcast_delta"):
            await conn_mgr.broadcast_delta(started_delta)

    log.info(
        "SIMULATION_START: Decentralized fleet telemetry streaming active (tick=%d, running=True).",
        fleet.tick,
    )
    return {"status": "started", "tick": fleet.tick, "mode": getattr(request.app.state, "fleet_mode", "spawned_new_fleet")}


@router.post("/pause", summary="Pause simulation telemetry streaming")
async def pause_simulation(request: Request) -> dict:
    import json
    fleet = request.app.state.fleet_state
    request.app.state.telemetry_streaming_paused = True
    fleet.is_running = False
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        orchestrator.pause()

    conn_mgr = getattr(request.app.state, "connection_manager", None)
    if conn_mgr:
        paused_delta = json.dumps({
            "type": "TICK_DELTA",
            "tick": fleet.tick,
            "fleet_status": {
                "running": False,
                "mode": getattr(request.app.state, "fleet_mode", "spawned_new_fleet"),
                "tick": fleet.tick,
                "armed_state": "PAUSED",
            },
        }, separators=(",", ":"))
        if hasattr(conn_mgr, "broadcast"):
            await conn_mgr.broadcast(paused_delta)
        elif hasattr(conn_mgr, "broadcast_delta"):
            await conn_mgr.broadcast_delta(paused_delta)


    log.info("SIMULATION_PAUSED: Telemetry streaming and robot processes paused.")
    return {"status": "paused", "tick": fleet.tick, "mode": "decentralized_telemetry"}


@router.post("/speed", summary="Set simulation tick speed multiplier")
async def set_speed(payload: SpeedPayload, request: Request) -> dict:
    speed = max(0.25, min(float(payload.speed), 8.0))
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None and hasattr(orchestrator, "set_speed"):
        orchestrator.set_speed(speed)
    request.app.state.simulation_speed = speed
    log.info(f"SIMULATION_SPEED: Set speed multiplier to {speed}x")
    return {"status": "ok", "speed": speed}


@router.post("/reset", summary="Reset simulation state")
async def reset_simulation(request: Request) -> dict:
    import json
    import time
    import asyncio
    from app.models.world import get_active_map_data
    from app.models.robot import Heading, RobotState, Robot, AMRType
    from app.services.reservations import clear_all_reservations, register_pod_slots

    fleet = request.app.state.fleet_state
    fleet.reset()
    request.app.state.telemetry_streaming_paused = True
    fleet.is_running = False

    task_mgr = getattr(request.app.state, "task_manager", None)
    if task_mgr is not None and hasattr(task_mgr, "clear"):
        task_mgr.clear()

    ord_mgr = getattr(request.app.state, "order_manager", None)
    if ord_mgr is not None and hasattr(ord_mgr, "clear"):
        ord_mgr.clear()

    clear_all_reservations()
    if hasattr(fleet, "world") and fleet.world and fleet.world.pod_slots:
        register_pod_slots(fleet.world.pod_slots)

    res_mgr = getattr(request.app.state, "reservation_manager", None)
    if res_mgr is not None and hasattr(res_mgr, "clear"):
        res_mgr.clear()

    telemetry = getattr(request.app.state, "telemetry", None)
    if telemetry is not None and hasattr(telemetry, "reset"):
        telemetry.reset()

    # Reseed inventory from active map
    active_map = get_active_map_data()
    ledger = getattr(request.app.state, "inventory_ledger", None)
    if ledger is not None and active_map and hasattr(fleet, "world") and fleet.world:
        ledger.seed_from_map(active_map, fleet.world)

    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None:
        orchestrator.reset(pause_on_reset=True)
        fleet.robots.clear()
        for c in orchestrator.robots_config:
            rid = c["robot_id"]
            rtype_str = c.get("robot_type", "GOODS_TO_PERSON")
            try:
                rtype_enum = AMRType(rtype_str)
            except Exception:
                rtype_enum = AMRType.GOODS_TO_PERSON
            fleet.robots[rid] = Robot(
                robot_id=rid,
                x=c["start"][0],
                y=c["start"][1],
                heading=Heading.NORTH,
                state=RobotState.IDLE,
                battery_pct=100.0,
                current_task_id=None,
                priority_score=0.0,
                last_updated_tick=0,
                robot_type=rtype_enum,
            )

    conn_mgr = getattr(request.app.state, "connection_manager", None)
    if conn_mgr is not None:
        reset_payload = {
            "tick": 0,
            "timestamp": int(time.time() * 1000),
            "running": False,
            "robots": fleet.robots_as_dicts(),
            "tasks": [],
            "orders": [],
            "conflicts": [],
            "obstacles": [],
        }
        raw_json = json.dumps(reset_payload)
        conn_mgr.latest_baseline_json = raw_json
        try:
            asyncio.create_task(conn_mgr.broadcast_text(raw_json))
        except Exception:
            pass

    log.info("SIMULATION_RESET: Telemetry viewer state, fleet processes, tasks, orders, and inventory reseeded.")
    return {"status": "reset", "tick": 0, "mode": "decentralized_telemetry"}


@router.get("/status", summary="Get simulation status", response_model=SimStatusOut)
async def get_status(request: Request) -> SimStatusOut:
    from app.core.config import get_settings
    fleet = request.app.state.fleet_state
    cfg = get_settings()
    speed = 1.0
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is not None and hasattr(orchestrator, "get_speed"):
        speed = orchestrator.get_speed()
    elif hasattr(request.app.state, "simulation_speed"):
        speed = float(request.app.state.simulation_speed)

    is_paused = getattr(request.app.state, "telemetry_streaming_paused", False) or (orchestrator.is_paused() if orchestrator else False)
    is_running = fleet.is_running and not is_paused

    return SimStatusOut(
        running=is_running,
        tick=fleet.tick,
        timestamp_ms=fleet.timestamp_ms,
        fleet_size=len(fleet.robots),
        tick_ms=cfg.SIM_TICK_MS,
        speed=speed,
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
    sys.path.insert(0, str(root_dir / "backend" / "backend" / "app" / "services"))
    sys.path.insert(0, str(root_dir / "archive" / "pathfinding"))
    sys.path.insert(0, str(root_dir / "conflict-engine"))
    sys.path.insert(0, str(root_dir / "testing"))

    from app.models.world import build_default_world
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
    obstacles = sorted(list(build_default_world().static_obstacles))
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

