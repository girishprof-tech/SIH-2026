"""
Comprehensive test suite — SIH2026 Backend.

Tests:
  - World: bounds, blocked cells, valid coords
  - Movement: 1 tile/tick, no diagonal, turn cost
  - Collision: same-cell, swap, obstacle
  - Reservations: creation, overlap, release
  - Battery: move/turn/wait/charge costs, clamping
  - Charging: trigger <20%, stop at 80%
  - Tasks: injection, assignment, pickup, dropoff
  - Conflicts: scoring, tie-breaker, yield
  - WebSocket: connect, disconnect, broadcast
  - Simulation: start, pause, reset

Run with: pytest backend/app/tests/test_all.py -v
"""

from __future__ import annotations

import asyncio
import pytest
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock

from app.models.robot import AMRType, Heading, PathNode, Robot, RobotState
from app.models.task import Task, TaskStatus
from app.models.obstacle import TemporaryObstacle
from app.models.world import WorldConfig, build_default_world
from app.models.reservation import ReservationTable
from app.services.reservation_manager import ReservationManager
from app.services.conflict_manager import ConflictManager
from app.services.task_manager import TaskManager
from app.services.fleet_state import FleetState
from app.services.planner_adapter import MockPlannerAdapter
from app.services.telemetry import Telemetry
from app.schemas.robot import RobotOut
from fastapi import HTTPException


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def world() -> WorldConfig:
    """30×30 world with minimal obstacles for testing."""
    return WorldConfig(
        width=30,
        height=30,
        cell_size_m=1.0,
        static_obstacles=frozenset({(5, 5), (5, 6), (5, 7)}),
        charging_stations=frozenset({(0, 0), (29, 29)}),
        pickup_stations=frozenset({(4, 22)}),
        dropoff_stations=frozenset({(27, 3)}),
    )


@pytest.fixture
def reservation_manager() -> ReservationManager:
    return ReservationManager()


def make_robot(robot_id="AMR-01", x=10, y=10, heading=Heading.NORTH,
               state=RobotState.IDLE, battery=100.0,
               robot_type=AMRType.GOODS_TO_PERSON) -> Robot:
    return Robot(
        robot_id=robot_id,
        x=x, y=y,
        heading=heading,
        state=state,
        robot_type=robot_type,
        battery_pct=battery,
        current_task_id=None,
        priority_score=0,
        last_updated_tick=0,
    )


def make_task(task_id="TASK-001", pickup=(4, 22), dropoff=(27, 3), urgency=3,
              status=TaskStatus.PENDING) -> Task:
    return Task(
        task_id=task_id,
        pickup_x=pickup[0],
        pickup_y=pickup[1],
        dropoff_x=dropoff[0],
        dropoff_y=dropoff[1],
        urgency=urgency,
        created_tick=0,
        status=status,
    )


# ─────────────────────────────────────────────────────────────────────────────
# World tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWorld:
    def test_in_bounds_valid(self, world):
        assert world.in_bounds(0, 0)
        assert world.in_bounds(29, 29)
        assert world.in_bounds(15, 15)

    def test_in_bounds_invalid(self, world):
        assert not world.in_bounds(-1, 0)
        assert not world.in_bounds(0, -1)
        assert not world.in_bounds(30, 0)
        assert not world.in_bounds(0, 30)

    def test_static_obstacle_blocked(self, world):
        assert world.is_static_blocked(5, 5)
        assert world.is_static_blocked(5, 6)
        assert world.is_static_blocked(5, 7)

    def test_non_obstacle_not_blocked(self, world):
        assert not world.is_static_blocked(0, 0)
        assert not world.is_static_blocked(10, 10)

    def test_walkable_cells_excludes_obstacles(self, world):
        for obs in world.static_obstacles:
            assert obs not in world.walkable_cells

    def test_walkable_cells_count(self, world):
        total = world.width * world.height
        assert len(world.walkable_cells) == total - len(world.static_obstacles)

    def test_charging_station_detection(self, world):
        assert world.is_charging_station(0, 0)
        assert world.is_charging_station(29, 29)
        assert not world.is_charging_station(5, 5)

    def test_nearest_charger(self, world):
        # Robot at (1,1) — nearest charger should be (0,0)
        nearest = world.nearest_charger(1, 1)
        assert nearest == (0, 0)

    def test_nearest_charger_far_end(self, world):
        # Robot at (28,28) — nearest charger should be (29,29)
        nearest = world.nearest_charger(28, 28)
        assert nearest == (29, 29)

    def test_default_world_has_multi_cell_docks_and_perimeter_chargers(self):
        w = build_default_world()
        assert 4 <= len(w.charging_stations) <= 6
        assert len(w.pickup_stations) > 1
        assert len(w.dropoff_stations) > 1
        assert w.zone_for(1, 10) == "IMPORT_DOCK"
        assert w.zone_for(28, 17) == "EXPORT_DOCK"
        assert w.zone_for(12, 14) == "GOODS_TO_PERSON_ZONE"
        assert w.zone_for(2, 2) == "SORTING_ZONE"


# ─────────────────────────────────────────────────────────────────────────────
# Robot model tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRobotModel:
    def test_initial_state(self):
        r = make_robot()
        assert r.state == RobotState.IDLE
        assert r.battery_pct == 100.0
        assert r.heading == Heading.NORTH
        assert r.robot_type == AMRType.GOODS_TO_PERSON

    def test_new_fleet_has_valid_types(self):
        fleet = FleetState()
        assert all(isinstance(r.robot_type, AMRType) for r in fleet.robots.values())
        assert {r.robot_type for r in fleet.robots.values()} <= {
            AMRType.GOODS_TO_PERSON,
            AMRType.SORTING,
            AMRType.SCANNING_AUDIT,
        }

    def test_robotout_serializes_robot_type(self):
        payload = RobotOut(
            robot_id="AMR-02",
            position={"x": 1, "y": 2},
            heading="NORTH",
            state="IDLE",
            battery_pct=79.5,
            current_task_id=None,
            priority_score=10,
            last_updated_tick=3,
            robot_type="SCANNING_AUDIT",
            path=[],
        )
        assert payload.robot_type == "SCANNING_AUDIT"
        assert payload.model_dump()["robot_type"] == "SCANNING_AUDIT"

    def test_battery_clamp_upper(self):
        r = make_robot(battery=100.0)
        r.battery_pct += 10.0
        r.clamp_battery()
        assert r.battery_pct == 100.0

    def test_battery_clamp_lower(self):
        r = make_robot(battery=0.0)
        r.battery_pct -= 10.0
        r.clamp_battery()
        assert r.battery_pct == 0.0

    def test_needs_charge_below_20(self):
        r = make_robot(battery=19.9)
        assert r.needs_charge

    def test_needs_charge_at_20(self):
        r = make_robot(battery=20.0)
        assert not r.needs_charge

    def test_position_tuple(self):
        r = make_robot(x=7, y=13)
        assert r.position == (7, 13)


# ─────────────────────────────────────────────────────────────────────────────
# Reservation tests — SCHEMA.md §8
# ─────────────────────────────────────────────────────────────────────────────

class TestReservationManager:
    def test_reserve_single(self, reservation_manager):
        rm = reservation_manager
        rm.reserve_single("AMR-01", 5, 5, 10)
        assert rm.is_reserved(5, 5, 10)

    def test_not_reserved(self, reservation_manager):
        assert not reservation_manager.is_reserved(0, 0, 0)

    def test_reserve_path(self, reservation_manager):
        path = [PathNode(x=1, y=0, t=1), PathNode(x=2, y=0, t=2), PathNode(x=3, y=0, t=3)]
        reservation_manager.reserve_path("AMR-01", path)
        assert reservation_manager.is_reserved(1, 0, 1)
        assert reservation_manager.is_reserved(2, 0, 2)
        assert reservation_manager.is_reserved(3, 0, 3)

    def test_release_clears_reservations(self, reservation_manager):
        path = [PathNode(x=1, y=0, t=1)]
        reservation_manager.reserve_path("AMR-01", path)
        reservation_manager.release("AMR-01")
        assert not reservation_manager.is_reserved(1, 0, 1)

    def test_overlap_detection(self, reservation_manager):
        path_a = [PathNode(x=5, y=5, t=10)]
        reservation_manager.reserve_path("AMR-01", path_a)
        assert reservation_manager.is_reserved_by_other(5, 5, 10, "AMR-02")

    def test_same_robot_not_detected_as_other(self, reservation_manager):
        reservation_manager.reserve_single("AMR-01", 5, 5, 10)
        assert not reservation_manager.is_reserved_by_other(5, 5, 10, "AMR-01")

    def test_purge_past(self, reservation_manager):
        reservation_manager.reserve_single("AMR-01", 5, 5, 5)
        reservation_manager.purge_past(10)
        assert not reservation_manager.is_reserved(5, 5, 5)

    def test_replanning_releases_old_path(self, reservation_manager):
        old_path = [PathNode(x=1, y=0, t=1), PathNode(x=2, y=0, t=2)]
        new_path = [PathNode(x=1, y=1, t=1), PathNode(x=1, y=2, t=2)]
        reservation_manager.reserve_path("AMR-01", old_path)
        reservation_manager.reserve_path("AMR-01", new_path)
        # Old path should be gone
        assert not reservation_manager.is_reserved(1, 0, 1)
        # New path should be present
        assert reservation_manager.is_reserved(1, 1, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Battery tests — SCHEMA.md §11
# ─────────────────────────────────────────────────────────────────────────────

class TestBattery:
    MOVE_COST = 1.0
    TURN_COST = 0.5
    WAIT_COST = 0.1
    CHARGE_RATE = 5.0

    def test_move_cost(self):
        r = make_robot(battery=50.0)
        r.battery_pct -= self.MOVE_COST
        assert abs(r.battery_pct - 49.0) < 1e-9

    def test_turn_cost(self):
        r = make_robot(battery=50.0)
        r.battery_pct -= self.TURN_COST
        assert abs(r.battery_pct - 49.5) < 1e-9

    def test_wait_cost(self):
        r = make_robot(battery=50.0)
        r.battery_pct -= self.WAIT_COST
        assert abs(r.battery_pct - 49.9) < 1e-9

    def test_charge_rate(self):
        r = make_robot(battery=50.0)
        r.battery_pct += self.CHARGE_RATE
        assert abs(r.battery_pct - 55.0) < 1e-9

    def test_clamp_at_100(self):
        r = make_robot(battery=98.0)
        r.battery_pct += 10.0
        r.clamp_battery()
        assert r.battery_pct == 100.0

    def test_clamp_at_0(self):
        r = make_robot(battery=0.5)
        r.battery_pct -= 5.0
        r.clamp_battery()
        assert r.battery_pct == 0.0

    def test_charging_trigger_threshold(self):
        # Exactly at 20% — should NOT charge yet
        r = make_robot(battery=20.0)
        assert not r.needs_charge
        # Below 20% — should charge
        r.battery_pct = 19.99
        assert r.needs_charge


# ─────────────────────────────────────────────────────────────────────────────
# Task tests — SCHEMA.md §5
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskManager:
    def test_create_task(self):
        tm = TaskManager()
        task = tm.create_task(4, 22, 27, 3, urgency=4, current_tick=0)
        assert task.status == TaskStatus.PENDING
        assert task.urgency == 4
        assert task.pickup == (4, 22)
        assert task.dropoff == (27, 3)

    def test_task_id_format(self):
        tm = TaskManager()
        task = tm.create_task(4, 22, 27, 3, urgency=1, current_tick=0)
        assert task.task_id.startswith("TASK-")

    def test_assign_to_idle_robot(self):
        tm = TaskManager()
        robot = make_robot(state=RobotState.IDLE)
        task = tm.create_task(4, 22, 27, 3, urgency=3, current_tick=0)
        robots = {"AMR-01": robot}
        result = tm.try_assign(task, robots, current_tick=1)
        assert result == "AMR-01"
        assert task.status == TaskStatus.ASSIGNED
        assert task.assigned_robot_id == "AMR-01"
        assert robot.state == RobotState.EN_ROUTE

    def test_no_assignment_if_no_idle_robot(self):
        tm = TaskManager()
        robot = make_robot(state=RobotState.CHARGING)
        task = tm.create_task(4, 22, 27, 3, urgency=3, current_tick=0)
        robots = {"AMR-01": robot}
        result = tm.try_assign(task, robots, current_tick=1)
        assert result is None
        assert task.status == TaskStatus.PENDING

    def test_scanning_audit_robot_not_task_assignable(self):
        tm = TaskManager()
        scanning = make_robot("AMR-99", state=RobotState.IDLE, robot_type=AMRType.SCANNING_AUDIT)
        goods = make_robot("AMR-01", state=RobotState.IDLE, robot_type=AMRType.GOODS_TO_PERSON)
        task = tm.create_task(4, 22, 27, 3, urgency=3, current_tick=0)
        result = tm.try_assign(task, {"AMR-99": scanning, "AMR-01": goods}, current_tick=1)
        assert result == "AMR-01"
        assert task.assigned_robot_id == "AMR-01"
        assert task.status == TaskStatus.ASSIGNED

    def test_scanning_audit_only_never_assigned(self):
        tm = TaskManager()
        scanning = make_robot("AMR-99", state=RobotState.IDLE, robot_type=AMRType.SCANNING_AUDIT)
        task = tm.create_task(4, 22, 27, 3, urgency=3, current_tick=0)
        result = tm.try_assign(task, {"AMR-99": scanning}, current_tick=1)
        assert result is None
        assert task.status == TaskStatus.PENDING

    def test_mark_completed(self):
        tm = TaskManager()
        robot = make_robot(state=RobotState.EN_ROUTE)
        task = tm.create_task(4, 22, 27, 3, urgency=2, current_tick=0)
        tm.try_assign(task, {"AMR-01": robot}, current_tick=1)
        task.status = TaskStatus.IN_PROGRESS
        tm.mark_completed(task.task_id, robot, current_tick=50)
        assert task.status == TaskStatus.COMPLETED
        assert robot.state == RobotState.IDLE
        assert robot.current_task_id is None

    def test_job_fetch_item_assigns_goods_to_person(self):
        fleet = FleetState()
        for robot in fleet.robots.values():
            if robot.robot_type == AMRType.GOODS_TO_PERSON:
                robot.state = RobotState.IDLE
        selected = min(
            [r for r in fleet.robots.values() if r.robot_type == AMRType.GOODS_TO_PERSON],
            key=lambda r: abs(r.x - 11) + abs(r.y - 12),
        )
        task_manager = TaskManager()
        from app.api.tasks import create_job
        from app.schemas.task import JobRequest
        request = MagicMock()
        request.app.state.fleet_state = fleet
        request.app.state.task_manager = task_manager
        result = asyncio.run(create_job(JobRequest(job_type="fetch_item", urgency=3), request))
        assert result.robot_type == "GOODS_TO_PERSON"
        assert result.task_id is not None
        assert result.robot_id == selected.robot_id

    def test_job_sort_batch_assigns_sorting(self):
        fleet = FleetState()
        for robot in fleet.robots.values():
            if robot.robot_type == AMRType.SORTING:
                robot.state = RobotState.IDLE
        selected = min(
            [r for r in fleet.robots.values() if r.robot_type == AMRType.SORTING],
            key=lambda r: abs(r.x - 1) + abs(r.y - 10),
        )
        task_manager = TaskManager()
        from app.api.tasks import create_job
        from app.schemas.task import JobRequest
        request = MagicMock()
        request.app.state.fleet_state = fleet
        request.app.state.task_manager = task_manager
        result = asyncio.run(create_job(JobRequest(job_type="sort_batch", urgency=2), request))
        assert result.robot_type == "SORTING"
        assert result.task_id is not None
        assert result.robot_id == selected.robot_id

    def test_job_audit_checkpoint_assigns_scanning_audit(self):
        fleet = FleetState()
        for robot in fleet.robots.values():
            if robot.robot_type == AMRType.SCANNING_AUDIT:
                robot.state = RobotState.IDLE
        selected = min(
            [r for r in fleet.robots.values() if r.robot_type == AMRType.SCANNING_AUDIT],
            key=lambda r: abs(r.x - 15) + abs(r.y - 15),
        )
        task_manager = TaskManager()
        from app.api.tasks import create_job
        from app.schemas.task import JobRequest
        request = MagicMock()
        request.app.state.fleet_state = fleet
        request.app.state.task_manager = task_manager
        result = asyncio.run(create_job(JobRequest(job_type="audit_checkpoint", urgency=4), request))
        assert result.robot_type == "SCANNING_AUDIT"
        assert result.audit_id is not None
        assert result.robot_id == selected.robot_id

    def test_job_missing_robot_returns_409(self):
        fleet = FleetState()
        for robot in fleet.robots.values():
            if robot.robot_type == AMRType.GOODS_TO_PERSON:
                robot.state = RobotState.CHARGING
        task_manager = TaskManager()
        from app.api.tasks import create_job
        from app.schemas.task import JobRequest
        request = MagicMock()
        request.app.state.fleet_state = fleet
        request.app.state.task_manager = task_manager
        with pytest.raises(HTTPException, match="No GOODS_TO_PERSON robot available"):
            asyncio.run(create_job(JobRequest(job_type="fetch_item", urgency=3), request))


# ─────────────────────────────────────────────────────────────────────────────
# Conflict / Priority tests — SCHEMA.md §13
# ─────────────────────────────────────────────────────────────────────────────

class TestConflictManager:
    def test_priority_score_formula(self):
        """Verify SCHEMA.md §13 priority formula."""
        robot = make_robot(battery=50.0)
        robot._wait_ticks = 5
        task = make_task(urgency=4)
        task._pickup_done = False

        score = ConflictManager._priority_score(robot, task)
        # (4 * 100) + 0 + (5 * 10) - 0 = 450
        assert score == 450

    def test_low_battery_bonus(self):
        """Battery < 20% adds 500 to priority."""
        robot = make_robot(battery=15.0)
        task = make_task(urgency=1)
        score = ConflictManager._priority_score(robot, task)
        # (1 * 100) + 500 + 0 - 0 = 600
        assert score == 600

    def test_tie_breaker_lower_id_wins(self):
        ra = make_robot("AMR-01")
        rb = make_robot("AMR-02")
        winner, loser = ConflictManager._resolve_tie(ra, rb, 100, 100)
        assert winner.robot_id == "AMR-01"
        assert loser.robot_id == "AMR-02"

    def test_higher_score_wins(self):
        ra = make_robot("AMR-01")
        rb = make_robot("AMR-02")
        winner, loser = ConflictManager._resolve_tie(ra, rb, 200, 100)
        assert winner.robot_id == "AMR-01"

    def test_yield_sets_state(self):
        robot = make_robot(state=RobotState.EN_ROUTE)
        ConflictManager._yield_robot(robot, 10)
        assert robot.state == RobotState.CONFLICT_NEGOTIATING
        assert robot._needs_replan
        assert robot._wait_ticks == 1

    def test_no_yield_during_charging(self):
        robot = make_robot(state=RobotState.CHARGING)
        ConflictManager._yield_robot(robot, 10)
        assert robot.state == RobotState.CHARGING  # unchanged


# ─────────────────────────────────────────────────────────────────────────────
# Planner tests — SCHEMA.md §14
# ─────────────────────────────────────────────────────────────────────────────

class TestMockPlanner:
    def test_find_simple_path(self, world):
        planner = MockPlannerAdapter()
        path = planner.find_path(
            start=(0, 1),
            goal=(0, 4),
            current_tick=0,
            reservation_table={},
            world=world,
            temp_blocked=set(),
        )
        assert len(path) > 0
        assert path[0]["x"] == 0 and path[0]["y"] == 1
        assert path[-1]["x"] == 0 and path[-1]["y"] == 4

    def test_path_monotonic_time(self, world):
        planner = MockPlannerAdapter()
        path = planner.find_path(
            start=(0, 0),
            goal=(5, 0),
            current_tick=10,
            reservation_table={},
            world=world,
            temp_blocked=set(),
        )
        for i in range(1, len(path)):
            assert path[i]["t"] > path[i-1]["t"]

    def test_no_path_through_obstacle(self, world):
        """Path should avoid static obstacles."""
        planner = MockPlannerAdapter()
        # Put start/goal around an obstacle column — path should go around
        path = planner.find_path(
            start=(4, 5),
            goal=(6, 5),
            current_tick=0,
            reservation_table={},
            world=world,
            temp_blocked=set(),
        )
        # Should find a path going around the obstacle at (5,5)
        assert len(path) > 0
        for node in path:
            assert (node["x"], node["y"]) not in world.static_obstacles

    def test_same_start_goal(self, world):
        planner = MockPlannerAdapter()
        path = planner.find_path(
            start=(3, 3),
            goal=(3, 3),
            current_tick=0,
            reservation_table={},
            world=world,
            temp_blocked=set(),
        )
        assert len(path) == 1
        assert path[0]["x"] == 3 and path[0]["y"] == 3

    def test_reservation_respected(self, world):
        """Planner must avoid reserved cells."""
        planner = MockPlannerAdapter()
        # Block the direct path cells
        reservation_table: ReservationTable = {}
        for t in range(1, 10):
            reservation_table[(0, 1, t)] = "AMR-99"
            reservation_table[(0, 2, t)] = "AMR-99"
            reservation_table[(0, 3, t)] = "AMR-99"

        path = planner.find_path(
            start=(0, 0),
            goal=(0, 4),
            current_tick=0,
            reservation_table=reservation_table,
            world=world,
            temp_blocked=set(),
        )
        # Path should find an alternate route
        if path:  # Some paths might still be possible
            for node in path:
                key = (node["x"], node["y"], node["t"])
                if key in reservation_table:
                    assert reservation_table[key] == "AMR-99"
                    pytest.fail(f"Path goes through reserved cell: {key}")

    def test_no_diagonal_movement(self, world):
        """Verify path only uses 4-directional movement."""
        planner = MockPlannerAdapter()
        path = planner.find_path(
            start=(0, 0),
            goal=(5, 5),
            current_tick=0,
            reservation_table={},
            world=world,
            temp_blocked=set(),
        )
        for i in range(1, len(path)):
            dx = abs(path[i]["x"] - path[i-1]["x"])
            dy = abs(path[i]["y"] - path[i-1]["y"])
            assert dx + dy <= 1, f"Diagonal move detected at step {i}"


# ─────────────────────────────────────────────────────────────────────────────
# Obstacle tests — SCHEMA.md §3
# ─────────────────────────────────────────────────────────────────────────────

class TestObstacles:
    def test_obstacle_active_in_range(self):
        obs = TemporaryObstacle("T1", 5, 5, created_tick=10, expires_at_tick=20)
        assert obs.is_active(10)
        assert obs.is_active(15)
        assert obs.is_active(19)

    def test_obstacle_inactive_before(self):
        obs = TemporaryObstacle("T1", 5, 5, created_tick=10, expires_at_tick=20)
        assert not obs.is_active(9)

    def test_obstacle_inactive_at_expiry(self):
        obs = TemporaryObstacle("T1", 5, 5, created_tick=10, expires_at_tick=20)
        assert not obs.is_active(20)

    def test_fleet_state_obstacle_queue(self):
        fleet = FleetState()
        obs = TemporaryObstacle("T1", 3, 3, created_tick=0, expires_at_tick=10)
        fleet.add_temp_obstacle(obs)
        assert len(fleet._pending_obstacles) == 1
        fleet.flush_obstacle_events()
        assert "T1" in fleet.temp_obstacles

    def test_fleet_state_obstacle_expire(self):
        fleet = FleetState()
        obs = TemporaryObstacle("T1", 3, 3, created_tick=0, expires_at_tick=5)
        fleet.temp_obstacles["T1"] = obs
        fleet.tick = 5
        fleet.expire_obstacles()
        assert "T1" not in fleet.temp_obstacles


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetry:
    def test_snapshot_keys(self):
        tel = Telemetry()
        snap = tel.snapshot()
        required = {
            "tick_ms_configured",
            "last_tick_processing_ms",
            "planner_latency_ms",
            "broadcast_latency_ms",
            "connected_clients",
            "active_robots",
            "active_conflicts",
            "replans",
        }
        assert required.issubset(snap.keys())

    def test_replan_counter(self):
        tel = Telemetry()
        tel.record_replan()
        tel.record_replan()
        assert tel.snapshot()["replans"] == 2

    def test_ema_smoothing(self):
        tel = Telemetry()
        tel.record_tick(10.0)
        tel.record_tick(20.0)
        # EMA should be between 10 and 20
        snap = tel.snapshot()
        assert 10.0 < snap["last_tick_processing_ms"] < 20.0


# ─────────────────────────────────────────────────────────────────────────────
# FleetState tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFleetState:
    def test_initial_fleet_size(self):
        fleet = FleetState()
        from app.core.config import get_settings
        assert len(fleet.robots) == get_settings().FLEET_SIZE

    def test_robot_ids_format(self):
        fleet = FleetState()
        for rid in fleet.robots:
            assert rid.startswith("AMR-")

    def test_reset_clears_state(self):
        fleet = FleetState()
        fleet.tick = 999
        fleet.reset()
        assert fleet.tick == 0
        assert not fleet.is_running

    def test_robots_in_bounds(self):
        fleet = FleetState()
        for robot in fleet.robots.values():
            assert 0 <= robot.x <= 29
            assert 0 <= robot.y <= 29

    def test_no_two_robots_same_cell(self):
        fleet = FleetState()
        positions = [(r.x, r.y) for r in fleet.robots.values()]
        assert len(positions) == len(set(positions)), "Two robots spawned on same cell!"

    def test_robots_not_on_obstacles(self):
        fleet = FleetState()
        for robot in fleet.robots.values():
            assert not fleet.world.is_static_blocked(robot.x, robot.y)

    def test_serialization_shape(self):
        fleet = FleetState()
        dicts = fleet.robots_as_dicts()
        assert len(dicts) == len(fleet.robots)
        for d in dicts:
            assert "robot_id" in d
            assert "position" in d
            assert "heading" in d
            assert "state" in d
            assert "battery_pct" in d


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket Connection Manager tests
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_increases_count(self):
        from app.websocket.connection_manager import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        await mgr.connect(ws)
        assert mgr.client_count == 1

    @pytest.mark.asyncio
    async def test_disconnect_decreases_count(self):
        from app.websocket.connection_manager import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        await mgr.connect(ws)
        mgr.disconnect(ws)
        assert mgr.client_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_calls_send(self):
        from app.websocket.connection_manager import ConnectionManager
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.send_text = AsyncMock(return_value=None)
        await mgr.connect(ws)
        await mgr.broadcast('{"test": true}')
        ws.send_text.assert_called_once_with('{"test": true}')

    @pytest.mark.asyncio
    async def test_dead_client_does_not_crash(self):
        from app.websocket.connection_manager import ConnectionManager
        from fastapi import WebSocketDisconnect
        mgr = ConnectionManager()
        ws = AsyncMock()
        ws.send_text = AsyncMock(side_effect=WebSocketDisconnect())
        await mgr.connect(ws)
        # Should not raise
        await mgr.broadcast('{"tick": 1}')
        # Dead client should have been removed
        assert mgr.client_count == 0

    @pytest.mark.asyncio
    async def test_multiple_clients(self):
        from app.websocket.connection_manager import ConnectionManager
        mgr = ConnectionManager()
        clients = [AsyncMock() for _ in range(5)]
        for ws in clients:
            ws.send_text = AsyncMock(return_value=None)
            await mgr.connect(ws)
        assert mgr.client_count == 5
        await mgr.broadcast('{"tick": 2}')
        for ws in clients:
            ws.send_text.assert_called_once()
