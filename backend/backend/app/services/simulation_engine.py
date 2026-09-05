"""
SimulationEngine — SCHEMA.md §6, §7, §8, §9, §10, §11, §12.

This is the central simulation loop.

TICK PIPELINE (deterministic, in this exact order):
  1.  Advance tick counter
  2.  Flush pending external events (obstacles, tasks)
  3.  Expire old temporary obstacles
  4.  Process task assignment for pending tasks
  5.  Detect and resolve conflicts
  6.  Request replanning for robots that need it
  7.  Execute robot movement / turn / wait
  8.  Apply battery costs
  9.  Process pickup / dropoff operations
  10. Process charging
  11. Update priority scores
  12. Purge stale reservations
  13. Update telemetry
  14. Broadcast WebSocket state

SCHEMA.md movement rules:
  - 4-directional only
  - max 1 tile per tick
  - turning costs 1 full tick (robot stays in place)
  - all enum values are exact per SCHEMA.md §4

SCHEMA.md battery rules:
  - Move  = -1.0%
  - Turn  = -0.5%
  - Wait  = -0.1%
  - Charge= +5.0%
  - Clamp 0–100%

SCHEMA.md charging:
  - trigger < 20%
  - target  = 80%
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from app.core.config import get_settings
from app.models.obstacle import TemporaryObstacle
from app.models.robot import (
    HEADING_DELTA,
    OPPOSITE_HEADING,
    Heading,
    PathNode,
    Robot,
    RobotState,
)
from app.models.task import Task, TaskStatus
from app.services.conflict_manager import ConflictManager
from app.services.fleet_state import FleetState
from app.services.planner_adapter import AbstractPlannerAdapter, get_planner_adapter
from app.services.reservation_manager import ReservationManager
from app.services.task_manager import TaskManager
from app.services.telemetry import Telemetry
from app.websocket.connection_manager import ConnectionManager

import sys
from pathlib import Path

# Add conflict-engine directory to sys.path for Member 3 engine integration
_conflict_engine_dir = str(Path(__file__).resolve().parents[4] / "conflict-engine")
if _conflict_engine_dir not in sys.path:
    sys.path.insert(0, _conflict_engine_dir)

try:
    from conflict_engine import run_conflict_engine_tick
except ImportError:
    run_conflict_engine_tick = None

log = logging.getLogger(__name__)
cfg = get_settings()


class SimulationEngine:
    """
    Authoritative simulation loop — runs as a background asyncio task.

    Architecture:
      - Single asyncio task drives the entire simulation
      - All state lives in FleetState (in-memory)
      - REST handlers enqueue changes; engine drains them at tick boundary
      - WebSocket broadcast happens after tick processing (non-blocking)
    """

    def __init__(
        self,
        fleet_state: FleetState,
        reservation_manager: ReservationManager,
        task_manager: TaskManager,
        conflict_manager: ConflictManager,
        connection_manager: ConnectionManager,
        telemetry: Telemetry,
        planner: Optional[AbstractPlannerAdapter] = None,
    ) -> None:
        self._state = fleet_state
        self._reservations = reservation_manager
        self._tasks = task_manager
        self._conflicts = conflict_manager
        self._ws = connection_manager
        self._tel = telemetry
        self._planner = planner or get_planner_adapter()

        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._chaos_enabled: bool = False
        self._chaos_packet_loss: int = 0

        # Update telemetry config
        self._tel.tick_ms_configured = cfg.SIM_TICK_MS

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            log.warning("SimulationEngine already running")
            return
        self._running = True
        self._state.is_running = True
        self._loop_task = asyncio.create_task(self._run_loop(), name="sim_loop")
        log.info("SIMULATION_STARTED tick_ms=%d", cfg.SIM_TICK_MS)

    async def pause(self) -> None:
        self._running = False
        self._state.is_running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        log.info("SIMULATION_PAUSED tick=%d", self._state.tick)

    async def reset(self) -> None:
        was_running = self._running
        await self.pause()
        self._state.reset()
        self._reservations._table.clear()
        self._reservations._robot_keys.clear()
        self._tel.replans = 0
        self._tel.total_ticks = 0
        if was_running:
            await self.start()
        log.info("SIMULATION_RESET done")

    def set_chaos(self, enabled: bool, packet_loss_pct: int) -> None:
        self._chaos_enabled = enabled
        self._chaos_packet_loss = max(0, min(100, packet_loss_pct))
        log.info("CHAOS mode=%s packet_loss=%d%%", enabled, self._chaos_packet_loss)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """
        Simulation loop — SCHEMA.md §6: tick every SIM_TICK_MS milliseconds.

        Uses monotonic clock. Never calls time.sleep().
        """
        tick_duration = cfg.SIM_TICK_MS / 1000.0

        while self._running:
            t_start = time.monotonic()

            try:
                await self._tick()
            except Exception as exc:
                log.exception("TICK_ERROR tick=%d: %s", self._state.tick, exc)

            elapsed = time.monotonic() - t_start
            sleep_s = max(0.0, tick_duration - elapsed)
            await asyncio.sleep(sleep_s)

    # ── Tick Pipeline ─────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        t0 = time.monotonic()

        # ── Step 1: Advance tick ──────────────────────────────────────────────
        self._state.tick += 1
        self._state.timestamp_ms = int(time.time() * 1000)
        tick = self._state.tick

        # ── Step 2: Flush external events ─────────────────────────────────────
        self._state.flush_obstacle_events()
        new_tasks = self._state.flush_task_events()
        for task in new_tasks:
            self._tasks._tasks.setdefault(task.task_id, task)

        # ── Step 3: Expire obstacles ───────────────────────────────────────────
        self._state.expire_obstacles()

        # Compute currently blocked temp cells (used throughout tick)
        temp_blocked: Set[Tuple[int, int]] = self._state.get_active_temp_blocked()

        # ── Step 4: Assign pending tasks ──────────────────────────────────────
        self._tasks.process_pending(self._state.robots, tick)

        # ── Step 5: Conflict detection & resolution ───────────────────────────
        t_conflict = time.monotonic()

        # =========================================================================
        # MEMBER 3 INTEGRATION: Call run_conflict_engine_tick() from conflict-engine
        # =========================================================================
        if run_conflict_engine_tick is not None:
            def _pathfinder_callback(start, goal, cur_tick, res_table, robot_id=None, **kwargs):
                return self._planner.find_path(
                    start=start,
                    goal=goal,
                    current_tick=cur_tick,
                    reservation_table=res_table,
                    world=self._state.world,
                    temp_blocked=temp_blocked,
                )

            conflict_summary = run_conflict_engine_tick(
                robots=self._state.robots,
                tasks=self._tasks.all_tasks(),
                reservation_table=self._reservations.table,
                current_tick=tick,
                find_path_fn=_pathfinder_callback,
            )
            self._state.active_conflicts = conflict_summary.get("resolutions", [])
        else:
            # Fallback to local conflict manager if conflict-engine is not found
            self._state.active_conflicts = self._conflicts.detect_and_resolve(
                self._state.robots, self._tasks.all_tasks(), tick
            )
        # =========================================================================
        # END MEMBER 3 INTEGRATION
        # =========================================================================

        conflict_ms = (time.monotonic() - t_conflict) * 1000
        self._tel.record_conflict_resolution(conflict_ms)

        # ── Step 6: Replan robots that need it ────────────────────────────────
        for robot in self._state.robots.values():
            if robot._needs_replan:
                robot._needs_replan = False
                await self._replan_robot(robot, tick, temp_blocked)

        # Plan robots that just got tasks assigned but have no path
        for robot in self._state.robots.values():
            if robot.state == RobotState.EN_ROUTE and not robot.path:
                await self._plan_robot_task(robot, tick, temp_blocked)

        # Plan charging routes for low-battery robots
        for robot in self._state.robots.values():
            if robot.needs_charge and robot.state not in (
                RobotState.CHARGING, RobotState.CONFLICT_NEGOTIATING
            ):
                await self._plan_charging_route(robot, tick, temp_blocked)

        # ── Step 7: Execute movement ───────────────────────────────────────────
        for robot in self._state.robots.values():
            self._execute_robot_step(robot, tick, temp_blocked)

        # ── Step 8–10: Battery + charging + pickup/drop ───────────────────────
        for robot in self._state.robots.values():
            self._update_charging(robot, tick)
            self._update_pickup_drop(robot, tick)

        # ── Step 11: Update priority scores ───────────────────────────────────
        for robot in self._state.robots.values():
            task = self._tasks.get_task(robot.current_task_id or "")
            urgency = task.urgency if task else 1
            battery_bonus = 500 if robot.battery_pct < 20 else 0
            robot.priority_score = (
                urgency * 100 + battery_bonus + robot._wait_ticks * 10
                - robot.distance_to_goal()
            )

        # ── Step 12: Purge stale reservations ─────────────────────────────────
        self._reservations.purge_past(tick)

        # ── Step 13: Telemetry ────────────────────────────────────────────────
        tick_ms = (time.monotonic() - t0) * 1000
        self._tel.record_tick(tick_ms)
        self._tel.active_robots = len(self._state.robots)
        self._tel.active_conflicts = len(self._state.active_conflicts)
        self._tel.connected_clients = self._ws.client_count
        self._state.last_tick_processing_ms = tick_ms

        # ── Step 14: Broadcast ─────────────────────────────────────────────────
        await self._broadcast(tick)

    # ── Planning helpers ──────────────────────────────────────────────────────

    async def _plan_robot_task(
        self,
        robot: Robot,
        tick: int,
        temp_blocked: Set[Tuple[int, int]],
    ) -> None:
        """Plan path to pickup (then dropoff will be planned on pickup completion)."""
        task = self._tasks.get_task(robot.current_task_id or "")
        if not task:
            return
        goal = task.pickup if not task._pickup_done else task.dropoff
        await self._plan_to(robot, goal, tick, temp_blocked)

    async def _plan_charging_route(
        self,
        robot: Robot,
        tick: int,
        temp_blocked: Set[Tuple[int, int]],
    ) -> None:
        charger = self._state.world.nearest_charger(robot.x, robot.y)
        if charger is None:
            return
        robot._charger_target = charger
        robot.state = RobotState.EN_ROUTE
        await self._plan_to(robot, charger, tick, temp_blocked)
        log.info("ROBOT_LOW_BATTERY robot=%s battery=%.1f%% → charger=%s",
                 robot.robot_id, robot.battery_pct, charger)

    async def _replan_robot(
        self,
        robot: Robot,
        tick: int,
        temp_blocked: Set[Tuple[int, int]],
    ) -> None:
        """Release old reservations and replan."""
        self._reservations.release(robot.robot_id)
        robot.path.clear()
        robot._path_idx = 0

        if robot.state == RobotState.CHARGING or robot._charger_target:
            charger = robot._charger_target or self._state.world.nearest_charger(robot.x, robot.y)
            if charger:
                await self._plan_to(robot, charger, tick, temp_blocked)
        elif robot.current_task_id:
            await self._plan_robot_task(robot, tick, temp_blocked)

        self._tel.record_replan()
        robot._replan_count += 1
        log.info("PATH_REPLANNED robot=%s tick=%d", robot.robot_id, tick)

    async def _plan_to(
        self,
        robot: Robot,
        goal: Tuple[int, int],
        tick: int,
        temp_blocked: Set[Tuple[int, int]],
    ) -> None:
        t0 = time.monotonic()

        # Release old reservations before planning
        self._reservations.release(robot.robot_id)

        path_raw = self._planner.find_path(
            start=(robot.x, robot.y),
            goal=goal,
            current_tick=tick,
            reservation_table=self._reservations.table,
            world=self._state.world,
            temp_blocked=temp_blocked,
        )

        planner_ms = (time.monotonic() - t0) * 1000
        self._tel.record_planner(planner_ms)

        if not path_raw:
            log.warning("NO_PATH robot=%s → %s", robot.robot_id, goal)
            return

        # Convert raw dicts to PathNode objects
        robot.path = [PathNode(x=p["x"], y=p["y"], t=p["t"]) for p in path_raw]
        robot._path_idx = 1  # Index 0 is current position

        # Reserve the path
        self._reservations.reserve_path(robot.robot_id, robot.path)

    # ── Movement execution ────────────────────────────────────────────────────

    def _execute_robot_step(
        self,
        robot: Robot,
        tick: int,
        temp_blocked: Set[Tuple[int, int]],
    ) -> None:
        """
        Execute one step of robot movement per SCHEMA.md §7.

        States that block movement: CHARGING, EMERGENCY_STOP, CONFLICT_NEGOTIATING
        """
        if robot.state in (RobotState.CHARGING, RobotState.EMERGENCY_STOP):
            robot.battery_pct -= cfg.BATTERY_WAIT_COST
            robot.clamp_battery()
            return

        if robot.state == RobotState.CONFLICT_NEGOTIATING:
            # Robot yielded this tick — just wait, cost is wait
            robot.battery_pct -= cfg.BATTERY_WAIT_COST
            robot.clamp_battery()
            robot._wait_ticks += 1
            # After 1 yield tick, reset to EN_ROUTE for next tick
            if robot.current_task_id:
                robot.state = RobotState.EN_ROUTE
            else:
                robot.state = RobotState.IDLE
            return

        if robot.state == RobotState.IDLE:
            # Idle robots drain a tiny bit and maintain their cell reservation
            robot.battery_pct -= cfg.BATTERY_WAIT_COST
            robot.clamp_battery()
            self._reservations.reserve_single(robot.robot_id, robot.x, robot.y, tick)
            self._reservations.reserve_single(robot.robot_id, robot.x, robot.y, tick + 1)
            return

        # EN_ROUTE: process next path step
        if not robot.path or robot._path_idx >= len(robot.path):
            # Reached destination or no path
            robot.state = RobotState.IDLE
            self._reservations.release(robot.robot_id)
            self._reservations.reserve_single(robot.robot_id, robot.x, robot.y, tick)
            self._reservations.reserve_single(robot.robot_id, robot.x, robot.y, tick + 1)
            return

        next_node = robot.path[robot._path_idx]
        nx, ny = next_node.x, next_node.y
        cx, cy = robot.x, robot.y

        # Safety checks — never enter blocked cell
        if (nx, ny) in temp_blocked or self._state.world.is_static_blocked(nx, ny):
            robot._needs_replan = True
            robot.battery_pct -= cfg.BATTERY_WAIT_COST
            robot.clamp_battery()
            return

        # Safety check — bounds
        if not self._state.world.in_bounds(nx, ny):
            log.error("BOUNDARY_VIOLATION robot=%s target=(%d,%d)", robot.robot_id, nx, ny)
            robot.state = RobotState.EMERGENCY_STOP
            return

        # Reservation check — another robot may have grabbed the cell
        if self._reservations.is_reserved_by_other(nx, ny, tick, robot.robot_id):
            robot._needs_replan = True
            robot.battery_pct -= cfg.BATTERY_WAIT_COST
            robot.clamp_battery()
            return

        # Determine required heading
        dx, dy = nx - cx, ny - cy
        required_heading = _delta_to_heading(dx, dy)

        if required_heading is None:
            # Same cell — wait tick (no movement)
            robot.battery_pct -= cfg.BATTERY_WAIT_COST
            robot.clamp_battery()
            robot._path_idx += 1
            robot.last_updated_tick = tick
            return

        if robot.heading != required_heading:
            # TURNING TICK: costs 0.5%, robot stays in place (SCHEMA.md §7)
            robot.heading = required_heading
            robot._is_turning = True
            robot.battery_pct -= cfg.BATTERY_TURN_COST
            robot.clamp_battery()
            robot.last_updated_tick = tick
            log.debug("TURN robot=%s new_heading=%s tick=%d", robot.robot_id, required_heading, tick)
            return

        # MOVEMENT TICK: move to next cell
        robot._is_turning = False
        robot.x = nx
        robot.y = ny
        robot._path_idx += 1
        robot._wait_ticks = 0
        robot.battery_pct -= cfg.BATTERY_MOVE_COST
        robot.clamp_battery()
        robot.last_updated_tick = tick

        # Check arrival at charger
        if (nx, ny) == robot._charger_target:
            robot.state = RobotState.CHARGING
            robot._charger_target = None
            log.info("ROBOT_CHARGING robot=%s pos=(%d,%d)", robot.robot_id, nx, ny)

    # ── Charging ──────────────────────────────────────────────────────────────

    def _update_charging(self, robot: Robot, tick: int) -> None:
        """SCHEMA.md §12: +5% per tick while charging, stop at 80%."""
        if robot.state != RobotState.CHARGING:
            return
        robot.battery_pct += cfg.BATTERY_CHARGE_RATE
        robot.clamp_battery()
        if robot.battery_pct >= cfg.BATTERY_CHARGE_TARGET:
            robot.battery_pct = cfg.BATTERY_CHARGE_TARGET
            robot.state = RobotState.IDLE
            robot.path.clear()
            robot._path_idx = 0
            self._reservations.release(robot.robot_id)
            log.info("ROBOT_CHARGED robot=%s battery=%.0f%% tick=%d",
                     robot.robot_id, robot.battery_pct, tick)

    # ── Pickup / Dropoff ──────────────────────────────────────────────────────

    def _update_pickup_drop(self, robot: Robot, tick: int) -> None:
        """
        SCHEMA.md §10: pickup takes 1 tick, drop takes 1 tick.
        """
        task = self._tasks.get_task(robot.current_task_id or "")
        if not task:
            return

        # Check if robot is at pickup and pickup not done
        if (not task._pickup_done
                and (robot.x, robot.y) == task.pickup
                and task.status in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)):
            # Start/complete pickup (1 tick)
            if robot._operation_type != "pickup":
                robot._operation_type = "pickup"
                robot._operation_ticks_remaining = 1
            robot._operation_ticks_remaining -= 1
            if robot._operation_ticks_remaining <= 0:
                task._pickup_done = True
                task.status = TaskStatus.IN_PROGRESS
                robot._operation_type = None
                robot.path.clear()
                robot._path_idx = 0
                self._reservations.release(robot.robot_id)
                # Plan to dropoff
                asyncio.create_task(
                    self._plan_to(robot, task.dropoff, tick, self._state.get_active_temp_blocked())
                )
                log.info("PICKUP_DONE task=%s robot=%s tick=%d", task.task_id, robot.robot_id, tick)
            return

        # Check if robot is at dropoff and pickup is done
        if (task._pickup_done
                and (robot.x, robot.y) == task.dropoff
                and task.status == TaskStatus.IN_PROGRESS):
            if robot._operation_type != "dropoff":
                robot._operation_type = "dropoff"
                robot._operation_ticks_remaining = 1
            robot._operation_ticks_remaining -= 1
            if robot._operation_ticks_remaining <= 0:
                robot._operation_type = None
                self._tasks.mark_completed(task.task_id, robot, tick)
                log.info("DROPOFF_DONE task=%s robot=%s tick=%d", task.task_id, robot.robot_id, tick)

    # ── WebSocket Broadcast ───────────────────────────────────────────────────

    async def _broadcast(self, tick: int) -> None:
        """
        Serialize tick state ONCE and send to all connected clients.
        Applies chaos packet loss at the communication layer (not simulation state).
        SCHEMA.md §16 contract.
        """
        if self._ws.client_count == 0:
            return

        t0 = time.monotonic()

        payload = {
            "type": "TICK_UPDATE",
            "tick": tick,
            "timestamp_ms": self._state.timestamp_ms,
            "robots": self._state.robots_as_dicts(),
            "active_conflicts": self._state.conflicts_as_dicts(),
            "temporary_obstacles": self._state.obstacles_as_dicts(),
        }

        # Chaos: simulate packet loss at WS layer only (not simulation state)
        if self._chaos_enabled and self._chaos_packet_loss > 0:
            import random
            if random.randint(0, 99) < self._chaos_packet_loss:
                log.debug("CHAOS dropped tick=%d", tick)
                return

        serialized = json.dumps(payload, separators=(",", ":"))
        await self._ws.broadcast(serialized)

        broadcast_ms = (time.monotonic() - t0) * 1000
        self._tel.record_broadcast(broadcast_ms)


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _delta_to_heading(dx: int, dy: int) -> Optional[Heading]:
    """Convert (dx, dy) to Heading enum. Returns None for (0,0)."""
    if dx == 0 and dy == 0:
        return None
    if dx == 1 and dy == 0:
        return Heading.EAST
    if dx == -1 and dy == 0:
        return Heading.WEST
    if dx == 0 and dy == -1:
        return Heading.NORTH
    if dx == 0 and dy == 1:
        return Heading.SOUTH
    return None
