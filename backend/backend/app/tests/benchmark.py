"""
Performance Benchmark for SIH2026 Backend.

Tests tick processing time for 10, 25, 50, 100 robots.
Also benchmarks planner and reservation operations.

Usage:
    cd backend
    python -m app.tests.benchmark

Results are printed to stdout and saved to benchmark_results.json.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from typing import Dict, List

from app.models.obstacle import TemporaryObstacle
from app.models.robot import Heading, PathNode, Robot, RobotState
from app.models.task import Task, TaskStatus
from app.models.world import build_default_world
from app.services.conflict_manager import ConflictManager
from app.services.fleet_state import FleetState
from app.services.planner_adapter import MockPlannerAdapter
from app.services.reservation_manager import ReservationManager
from app.services.task_manager import TaskManager
from app.services.telemetry import Telemetry
from app.services.simulation_engine import SimulationEngine
from app.websocket.connection_manager import ConnectionManager


def make_fleet(n: int) -> Dict[str, Robot]:
    robots = {}
    for i in range(n):
        rid = f"AMR-{i+1:02d}"
        x = (i * 3) % 29
        y = (i // 10) * 3
        robots[rid] = Robot(
            robot_id=rid,
            x=x, y=y,
            heading=Heading.NORTH,
            state=RobotState.EN_ROUTE,
            battery_pct=100.0 - (i % 10),
            current_task_id=None,
            priority_score=0,
            last_updated_tick=0,
        )
    return robots


def bench_planner(n_plans: int = 100) -> dict:
    world = build_default_world()
    planner = MockPlannerAdapter()
    times = []
    for i in range(n_plans):
        sx, sy = i % 25, 0
        gx, gy = 25, 29
        t0 = time.monotonic()
        planner.find_path((sx, sy), (gx, gy), 0, {}, world, set())
        times.append((time.monotonic() - t0) * 1000)
    return {
        "n_plans": n_plans,
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
        "mean_ms": round(statistics.mean(times), 3),
        "p95_ms": round(sorted(times)[int(n_plans * 0.95)], 3),
    }


def bench_reservation(n_robots: int = 100, path_len: int = 30) -> dict:
    rm = ReservationManager()
    times = []
    for i in range(n_robots):
        rid = f"AMR-{i}"
        path = [PathNode(x=j % 29, y=(j // 29) % 29, t=j) for j in range(path_len)]
        t0 = time.monotonic()
        rm.reserve_path(rid, path)
        times.append((time.monotonic() - t0) * 1000)
    return {
        "n_robots": n_robots,
        "path_len": path_len,
        "mean_reserve_ms": round(statistics.mean(times), 4),
        "total_reservations": len(rm.table),
    }


async def bench_tick(fleet_size: int, n_ticks: int = 50) -> dict:
    """Run N ticks with fleet_size robots and measure processing time."""
    import os
    os.environ["FLEET_SIZE"] = str(fleet_size)

    # We'll directly test the tick pipeline components
    world = build_default_world()
    rm = ReservationManager()
    tel = Telemetry()
    tel.tick_ms_configured = 500
    cm = ConnectionManager()

    fleet = FleetState()
    # Override robot count
    fleet.robots = make_fleet(fleet_size)

    tm = TaskManager()
    cfm = ConflictManager(rm)

    times = []
    for tick in range(1, n_ticks + 1):
        fleet.tick = tick
        t0 = time.monotonic()

        # Simulate tick steps manually (without full engine overhead)
        temp_blocked = fleet.get_active_temp_blocked()
        conflicts = cfm.detect_and_resolve(fleet.robots, {}, tick)
        fleet.active_conflicts = conflicts
        _ = fleet.robots_as_dicts()

        times.append((time.monotonic() - t0) * 1000)

    return {
        "fleet_size": fleet_size,
        "n_ticks": n_ticks,
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
        "mean_ms": round(statistics.mean(times), 3),
        "p95_ms": round(sorted(times)[int(n_ticks * 0.95)], 3),
        "p99_ms": round(sorted(times)[int(n_ticks * 0.99)], 3),
        "budget_500ms_ok": max(times) < 500,
    }


async def main():
    print("=" * 60)
    print("SIH2026 Backend Performance Benchmark")
    print("=" * 60)

    results = {}

    # Planner benchmark
    print("\n[1/3] Planner (Space-Time A*) benchmark...")
    planner_results = bench_planner(n_plans=50)
    results["planner"] = planner_results
    print(f"  Plans: 50 | Mean: {planner_results['mean_ms']}ms | P95: {planner_results['p95_ms']}ms")

    # Reservation benchmark
    print("\n[2/3] Reservation table benchmark...")
    res_results = bench_reservation(n_robots=100, path_len=30)
    results["reservation"] = res_results
    print(f"  100 robots × 30-step path | Mean reserve: {res_results['mean_reserve_ms']}ms")

    # Tick benchmarks
    print("\n[3/3] Tick processing benchmark...")
    for fleet_size in [10, 25, 50, 100]:
        tick_res = await bench_tick(fleet_size, n_ticks=50)
        results[f"tick_{fleet_size}_robots"] = tick_res
        budget_ok = "OK" if tick_res["budget_500ms_ok"] else "FAIL"
        print(
            f"  {fleet_size:3d} robots | Mean: {tick_res['mean_ms']:6.3f}ms"
            f" | P95: {tick_res['p95_ms']:6.3f}ms | <500ms budget: {budget_ok}"
        )

    # Save results
    with open("benchmark_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to benchmark_results.json")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
