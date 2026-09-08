"""
robot_node.py — Decentralized Autonomous Robot Execution Unit.

ARCHITECTURAL PRINCIPLES:
  1. Each robot runs in its OWN independent OS process (via multiprocessing.Process).
  2. Pluggable Transport (UdpTransport over 127.0.0.1 or real LAN, LoopbackTransport for unit tests).
  3. Lightweight HMAC-SHA256 signing and ReplayGuard on all peer-to-peer envelopes.
  4. Authoritative Deterministic Finite State Machine (RobotFSM):
     Transitions:
       (IDLE, TASK_RECEIVED) -> ASSIGNED
       (IDLE, START_AUDIT) -> AUDITING
       (ASSIGNED, PATH_PLANNED) -> EN_ROUTE_PICKUP
       (EN_ROUTE_PICKUP, PICKUP_REACHED) -> PICKING
       (EN_ROUTE_PICKUP, CONFLICT_LOST) -> CONFLICT_NEGOTIATING
       (PICKING, PICKUP_COMPLETE) -> EN_ROUTE_DROPOFF
       (EN_ROUTE_DROPOFF, DROPOFF_REACHED) -> DROPPING
       (EN_ROUTE_DROPOFF, CONFLICT_LOST) -> CONFLICT_NEGOTIATING
       (DROPPING, MISSION_COMPLETE) -> IDLE
       (AUDITING, AUDIT_CHECKPOINT_LOGGED) -> IDLE
       (AUDITING, CONFLICT_LOST) -> CONFLICT_NEGOTIATING
       (CONFLICT_NEGOTIATING, RESUME_PICKUP) -> EN_ROUTE_PICKUP
       (CONFLICT_NEGOTIATING, RESUME_DROPOFF) -> EN_ROUTE_DROPOFF
       (CONFLICT_NEGOTIATING, RESUME_AUDIT) -> AUDITING
       (FAILSAFE_HOLD, FAILSAFE_RESET) -> IDLE
       (EMERGENCY_STOP, RESET) -> IDLE
  5. State Hygiene: self.pre_conflict_activity is purged immediately upon entering FAILSAFE_HOLD or IDLE.
  6. Deadlock/Livelock resolution: consecutive wait ticks >= 3 triggers alternate route or side-step nook.
  7. Auditing robots score at lowest priority tier floor (-1000.0).
  8. Degraded Mode: 50% speed throttle on missing peer ticks.
  9. Task Realism: 1-tick load pause every 4th step when carrying payload in EN_ROUTE_DROPOFF.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "archive" / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from grid import WarehouseGrid
from pathfinder import find_path
from reservations import reserve_path, release_reservations, prune_past
from priority import calculate_priority_score
from conflict_detector import detect_peer_conflict
from arbitration import resolve_peer_conflict
from models import Heading, Robot, Task
from app.models.robot_fsm import RobotEvent, RobotFSM, RobotState
from app.transport.base import Transport
from app.transport.udp_transport import UdpTransport
from app.security.hmac_envelope import sign_payload, verify_envelope
from app.security.replay_guard import ReplayGuard
from app.services.degraded_mode import DegradedModeDetector
from app.services.audit_mission import AuditMission
from app.ml.priority_gnn import compute_priority
from app.core.config import get_settings
from app.models.world import build_default_world

cfg = get_settings()


@dataclass
class PeerSnapshot:
    """Lightweight representation of a peer robot received via peer-to-peer message."""
    robot_id: str
    position: Tuple[int, int]
    intended_pos: Tuple[int, int]
    heading: Heading
    priority_score: float
    state: RobotState
    wait_ticks_so_far: int
    path: List[Dict[str, Any]]
    last_seen_tick: int
    charger_target: Optional[Tuple[int, int]] = None


class RobotNode:
    """
    Independent autonomous robot decision agent.
    Runs in its own process.
    """

    def __init__(
        self,
        robot_id: str,
        start_pos: Tuple[int, int],
        goal_pos: Optional[Tuple[int, int]] = None,
        urgency: int = 3,
        battery_pct: float = 100.0,
        obstacles: Optional[List[Tuple[int, int]]] = None,
        port: int = 9001,
        peer_ports: Optional[Dict[str, int]] = None,
        telemetry_queue: Optional[mp.Queue] = None,
        log_dir: Optional[Path] = None,
        tick_interval_s: float = 0.1,
        host: str = "127.0.0.1",
        transport: Optional[Transport] = None,
        secret_key: str = "sih2026-edge-robot-shared-secret",
        charging_stations: Optional[Set[Tuple[int, int]]] = None,
        robot_type: str = "GOODS_TO_PERSON",
        enable_idle_audit: bool = True,
    ) -> None:
        self.robot_id = robot_id
        self.start_pos = start_pos
        # A robot with no assigned task starts genuinely idle with no goal set (goal = None)
        if goal_pos is None or goal_pos == start_pos:
            self.goal_pos = None
        else:
            self.goal_pos = goal_pos
        self.urgency = urgency
        self.battery_pct = battery_pct
        self.obstacles = obstacles or []
        self.port = port
        self.peer_ports = peer_ports or {}
        self.host = host
        self.telemetry_queue = telemetry_queue
        self.tick_interval_s = tick_interval_s
        self.secret_key = secret_key
        default_world = build_default_world()
        self.charging_stations = charging_stations or set(default_world.charging_stations)
        self.dropoff_stations: Set[Tuple[int, int]] = set(default_world.dropoff_stations)
        self.pickup_stations: Set[Tuple[int, int]] = set(default_world.pickup_stations)
        self.charger_target: Optional[Tuple[int, int]] = None
        self.robot_type = robot_type
        self.enable_idle_audit = enable_idle_audit

        # 1. Logging
        if log_dir is None:
            log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / f"robot_{robot_id}.log"
        self._setup_logger()

        # 2. Pluggable Transport
        if transport is not None:
            self.transport = transport
        else:
            self.transport = UdpTransport(
                node_id=robot_id,
                port=port,
                peer_ports=self.peer_ports,
                host=host,
            )

        # 3. Security & Replay Guard
        self.seq = 0
        self.replay_guard = ReplayGuard(freshness_window_s=5.0)

        # 4. Grid and Local Reservations
        self.grid = WarehouseGrid(obstacles=self.obstacles, width=30, height=30)
        self.local_reservations: Dict[Tuple[int, int, int], str] = {}
        self.HOLD = 30

        # 5. Deterministic Finite State Machine
        self.fsm = RobotFSM(RobotState.IDLE)
        self.pre_conflict_activity: Optional[RobotState] = None
        self.failsafe_hold_ticks = 0

        # 6. Mission & Task Management
        self.task: Optional[Task] = None
        self.completed_task_ids: Set[str] = set()
        self.active_audit_mission: Optional[AuditMission] = None

        # 7. Robot Model Entity
        self.robot = Robot(
            robot_id=robot_id,
            position=start_pos,
            heading=Heading.NORTH,
            state=self.fsm.state,
            battery_pct=battery_pct,
            current_task_id=None,
            path=[],
            priority_score=0.0,
            wait_ticks_so_far=0,
            last_updated_tick=0,
        )

        # 8. Deadlock / Livelock Breaker State
        self.consecutive_wait_ticks = 0
        self.last_planner_ms: float = 0.0
        self.idle_ticks: int = 0

        # 9. Degraded Network Detector
        self.degraded_detector = DegradedModeDetector(threshold_missing_ticks=3)

        # 10. Task Realism Load Step Counter & Calibrated Battery Counter
        self.load_move_steps = 0
        self.battery_move_steps = 0

        # Perceived peer states
        self.peers: Dict[str, PeerSnapshot] = {}

        # If goal_pos provided at startup, auto-initialize initial task for legacy/demo scenarios
        if self.goal_pos is not None:
            self._assign_initial_task(self.goal_pos, self.urgency)

    def _timed_find_path(self, *args, **kwargs) -> List[Dict[str, Any]]:
        t0 = time.perf_counter()
        p = find_path(*args, **kwargs)
        self.last_planner_ms = (time.perf_counter() - t0) * 1000.0
        return p

    def _setup_logger(self) -> None:
        self.logger = logging.getLogger(f"RobotNode.{self.robot_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        fh = logging.FileHandler(self.log_file, mode="w", encoding="utf-8")
        formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)
        self.log(f"Autonomous Robot Node initialized. PID={os.getpid()}, Start={self.start_pos}, Goal={self.goal_pos}")

    def log(self, message: str) -> None:
        self.logger.info(message)

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass

    def _assign_initial_task(
        self,
        goal_pos: Tuple[int, int],
        urgency: int,
        payload_weight_kg: float = 0.0,
        task_id: Optional[str] = None,
        pickup_pos: Optional[Tuple[int, int]] = None,
    ) -> None:
        """Assigns an initial mission and plans the initial route."""
        tid = task_id or f"TASK-{self.robot_id}"
        p_pos = pickup_pos if pickup_pos is not None else self.start_pos
        self.task = Task(
            task_id=tid,
            pickup=p_pos,
            dropoff=goal_pos,
            urgency=urgency,
            created_tick=0,
            assigned_robot_id=self.robot_id,
            status="ASSIGNED",
            payload_weight_kg=payload_weight_kg,
        )
        self.robot.current_task_id = tid
        self.goal_pos = goal_pos
        self.fsm.transition(RobotEvent.TASK_RECEIVED)

        # Plan initial route to pickup (or dropoff if starting at pickup)
        target = self.task.dropoff if self.robot.position == self.task.pickup else self.task.pickup
        path = self._timed_find_path(
            start=self.robot.position,
            goal=target,
            current_tick=0,
            reservation_table=self.local_reservations,
            robot_id=self.robot.robot_id,
            grid=self.grid,
        )
        if path:
            self.robot.path = path
            reserve_path(path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
            self.fsm.transition(RobotEvent.PATH_PLANNED)
            self.log(f"Initial path planned ({len(path)} steps) to {target}.")
        else:
            self.robot.path = [{"x": self.robot.position[0], "y": self.robot.position[1], "t": 0}]
            reserve_path(self.robot.path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
            self.fsm.transition(RobotEvent.PATH_PLANNED)

        self.robot.state = self.fsm.state

    def _recover_from_failsafe(self, tick: int = 0) -> None:
        """Shared recovery logic for automatic watchdog and manual operator reset."""
        self.pre_conflict_activity = None
        self.failsafe_hold_ticks = 0
        if self.fsm.state == RobotState.EMERGENCY_STOP:
            self.fsm.transition(RobotEvent.RESET)
        elif self.fsm.state == RobotState.FAILSAFE_HOLD:
            self.fsm.transition(RobotEvent.FAILSAFE_RESET)
        self.robot.state = self.fsm.state
        self.log(f"[Tick {tick}] Recovered from failsafe/emergency stop -> IDLE.")
        if self.task and self.task.dropoff:
            orig_task_id = self.task.task_id
            self._assign_initial_task(
                goal_pos=self.task.dropoff,
                urgency=self.task.urgency,
                payload_weight_kg=getattr(self.task, "payload_weight_kg", 0.0),
                task_id=orig_task_id,
                pickup_pos=self.task.pickup,
            )

    def reset_failsafe(self) -> None:
        """Manual operator override command to recover from FAILSAFE_HOLD or EMERGENCY_STOP to IDLE."""
        self._recover_from_failsafe(tick=0)

    def step(self, tick: int) -> Dict[str, Any]:
        """
        Executes one autonomous tick loop step for this robot.
        """
        prev_pos = self.robot.position
        prev_heading = self.robot.heading
        action_taken = "IDLE"
        conflict_resolved = None

        # 0. Active Deterministic Priority Calculation for this Tick
        dist_to_goal = 0
        if self.goal_pos:
            dist_to_goal = abs(self.robot.position[0] - self.goal_pos[0]) + abs(self.robot.position[1] - self.goal_pos[1])
        self.robot.priority_score = calculate_priority_score(self.robot, self.task, dist_to_goal)

        # 1. Check Failsafe Watchdog
        if self.fsm.state == RobotState.FAILSAFE_HOLD:
            self.failsafe_hold_ticks += 1
            if self.failsafe_hold_ticks >= 5:
                self._recover_from_failsafe(tick=tick)
            else:
                self.log(f"[Tick {tick}] In FAILSAFE_HOLD ({self.failsafe_hold_ticks}/5 ticks). Holding position.")
                return self._build_telemetry_frame(tick, "HOLDING", None)

        # 2. Drain incoming transport messages
        self._drain_inbox(tick)

        # 2.5. Dynamic Dock Re-targeting (avoid queuing on occupied docks)
        if self.goal_pos and self.goal_pos in self.dropoff_stations and self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.EN_ROUTE_DROPOFF):
            peer_occupying_goal = any(
                p.position == self.goal_pos for p in self.peers.values()
                if p.robot_id != self.robot.robot_id and p.last_seen_tick >= tick - 3
            )
            if peer_occupying_goal:
                peer_occupied_cells = {p.position for p in self.peers.values()} | {p.intended_pos for p in self.peers.values()}
                open_docks = [d for d in self.dropoff_stations if d not in peer_occupied_cells]
                if open_docks:
                    best_dock = min(open_docks, key=lambda d: abs(d[0] - self.robot.position[0]) + abs(d[1] - self.robot.position[1]))
                    self.log(f"[Tick {tick}] Assigned dropoff dock {self.goal_pos} occupied; dynamically retargeting to open dock {best_dock}.")
                    self.goal_pos = best_dock
                    if self.task:
                        self.task.dropoff = best_dock
                    self.robot.path = []

        # 2.6. Idle Dock Clearance & Proactive Evasion for Working AMRs
        if self.fsm.state == RobotState.IDLE and not self.task and not self.charger_target:
            rx, ry = self.robot.position
            peer_positions = {p.position for p in self.peers.values()}
            peer_intents = {p.intended_pos for p in self.peers.values()}

            # If idle robot is sitting inside an active dropoff dock, vacate into highway corridor
            if (rx, ry) in self.dropoff_stations or (rx, ry) in self.pickup_stations:
                clear_candidates = [(rx - 1, ry), (rx + 1, ry), (rx, ry - 1), (rx, ry + 1)]
                for cand in clear_candidates:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if self.grid.is_free(cand) and cand not in peer_positions and cand not in peer_intents and cand not in self.dropoff_stations and cand not in self.pickup_stations:
                            intended_pos = cand
                            action_taken = "VACATED_DOCK"
                            self.robot.path = [{"x": rx, "y": ry, "t": tick}, {"x": cand[0], "y": cand[1], "t": tick + 1}]
                            self.log(f"[Tick {tick}] Vacating active dock bay ({rx}, {ry}) -> {cand} to keep transfer zone clear.")
                            break

            # If an active working robot is targeting our cell or approaching within 1 cell, yield by stepping aside
            peer_targeting_us = any(
                p.intended_pos == (rx, ry) or (abs(p.position[0] - rx) + abs(p.position[1] - ry) <= 1 and p.intended_pos == (rx, ry))
                for p in self.peers.values()
                if p.robot_id != self.robot.robot_id and p.state != "IDLE"
            )
            if peer_targeting_us and intended_pos == self.robot.position:
                evade_candidates = [(rx, ry - 1), (rx, ry + 1), (rx - 1, ry), (rx + 1, ry)]
                for cand in evade_candidates:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if self.grid.is_free(cand) and cand not in peer_positions and cand not in peer_intents and cand not in self.dropoff_stations:
                            intended_pos = cand
                            action_taken = "EVADED / STEPPED_ASIDE"
                            self.robot.path = [{"x": rx, "y": ry, "t": tick}, {"x": cand[0], "y": cand[1], "t": tick + 1}]
                            self.log(f"[Tick {tick}] Idle node proactively stepping aside from ({rx}, {ry}) to {cand} for oncoming active peer.")
                            break

        # Check battery threshold & charging
        if self.fsm.state == RobotState.CHARGING:
            self.robot.battery_pct = min(100.0, self.robot.battery_pct + 4.0)
            if self.robot.battery_pct >= 95.0:
                self.fsm.transition(RobotEvent.CHARGE_COMPLETE)
                self.robot.state = self.fsm.state
                self.charger_target = None
                self.goal_pos = None
                self.robot.path = []
                self.log(f"[Tick {tick}] Charging complete ({self.robot.battery_pct:.1f}%). Returning to IDLE.")
            return self._build_telemetry_frame(tick, "CHARGING", None)

        if self.robot.battery_pct <= 25.0 and self.fsm.state != RobotState.CHARGING:
            if self.charger_target is None:
                self.charger_target = self._nearest_available_charger()
                if self.charger_target is not None:
                    self.goal_pos = self.charger_target
                    self.fsm.state = RobotState.EN_ROUTE_PICKUP
                    self.robot.state = self.fsm.state
                    charging_path = self._timed_find_path(
                        start=self.robot.position,
                        goal=self.charger_target,
                        current_tick=tick,
                        reservation_table=self.local_reservations,
                        robot_id=self.robot.robot_id,
                        grid=self.grid,
                    )
                    if charging_path and len(charging_path) > 1:
                        self.robot.path = charging_path
                        reserve_path(charging_path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
                    self.log(f"[Tick {tick}] Low battery ({self.robot.battery_pct:.1f}%) routing to charger {self.charger_target}.")
            if self.charger_target is None:
                self.log(f"[Tick {tick}] All charging stations occupied; holding at {self.robot.position}.")
                return self._build_telemetry_frame(tick, "CHARGER_QUEUE_WAIT", None)

        # Check idle background audit patrol trigger
        if self.enable_idle_audit and self.fsm.state == RobotState.IDLE and not self.task:
            self.idle_ticks += 1
            if self.idle_ticks >= 10:
                self.idle_ticks = 0
                from app.services.audit_mission import DEFAULT_CHECKPOINTS, AuditMission
                checkpoints = [c for c in DEFAULT_CHECKPOINTS if c != self.robot.position] or DEFAULT_CHECKPOINTS
                best_cp = min(checkpoints, key=lambda cp: abs(cp[0] - self.robot.position[0]) + abs(cp[1] - self.robot.position[1]))
                self.active_audit_mission = AuditMission(best_cp)
                self.goal_pos = best_cp
                self.fsm.transition(RobotEvent.START_AUDIT)
                self.robot.state = self.fsm.state
                audit_path = self._timed_find_path(
                    start=self.robot.position,
                    goal=best_cp,
                    current_tick=tick,
                    reservation_table=self.local_reservations,
                    grid=self.grid,
                )
                if audit_path and len(audit_path) > 1:
                    self.robot.path = audit_path
                    reserve_path(audit_path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
                self.log(f"[Tick {tick}] Triggered background audit mission to {best_cp}.")
        else:
            self.idle_ticks = 0

        # 3. Handle Atomic PICKING / DROPPING ticks
        if self.fsm.state == RobotState.PICKING:
            self.log(f"[Tick {tick}] Executing atomic pickup at {self.robot.position}...")
            self.fsm.transition(RobotEvent.PICKUP_COMPLETE)
            if self.task:
                self.task.status = "IN_PROGRESS"
                # Plan route to dropoff
                p_drop = self._timed_find_path(
                    start=self.robot.position,
                    goal=self.task.dropoff,
                    current_tick=tick,
                    reservation_table=self.local_reservations,
                    grid=self.grid,
                )
                if p_drop and len(p_drop) > 1:
                    self.robot.path = p_drop
                    reserve_path(p_drop, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
            self.robot.state = self.fsm.state
            return self._build_telemetry_frame(tick, "PICKING_COMPLETE", None)

        if self.fsm.state == RobotState.DROPPING:
            self.log(f"[Tick {tick}] Executing atomic dropoff at {self.robot.position}...")
            if self.task:
                self.completed_task_ids.add(self.task.task_id)
                self.task.status = "COMPLETED"
                self.task = None
            self.goal_pos = None
            self.robot.path = []
            self.load_move_steps = 0
            self.fsm.transition(RobotEvent.MISSION_COMPLETE)
            self.robot.state = self.fsm.state
            self.pre_conflict_activity = None
            return self._build_telemetry_frame(tick, "MISSION_COMPLETED", None)

        # 4. Propose Next Position along Path (if not already evading / vacating)
        if intended_pos == self.robot.position:
            if self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.EN_ROUTE_DROPOFF, RobotState.AUDITING, RobotState.CONFLICT_NEGOTIATING):
                if not self.robot.path or len(self.robot.path) <= 1:
                    # Path exhausted: replan if goal exists
                    if self.goal_pos and self.robot.position != self.goal_pos:
                        re_p = self._timed_find_path(
                            start=self.robot.position,
                            goal=self.goal_pos,
                            current_tick=tick,
                            reservation_table=self.local_reservations,
                            grid=self.grid,
                        )
                        if re_p and len(re_p) > 1:
                            self.robot.path = re_p
                            intended_pos = (int(self.robot.path[1]["x"]), int(self.robot.path[1]["y"]))
                        else:
                            intended_pos = self.robot.position
                elif len(self.robot.path) > 1:
                    intended_pos = (int(self.robot.path[1]["x"]), int(self.robot.path[1]["y"]))

        # 6. Deadlock / Livelock Breaker (Phase 0 Fix)
        if (
            self.consecutive_wait_ticks >= 3
            and self.goal_pos
            and self.robot.position != self.goal_pos
            and self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.EN_ROUTE_DROPOFF, RobotState.CONFLICT_NEGOTIATING, RobotState.AUDITING)
        ):
            self.log(f"[Tick {tick}] Deadlock/livelock detected ({self.consecutive_wait_ticks} wait ticks). Seeking alternate spatial route/nook...")
            for k in [k for k, v in list(self.local_reservations.items()) if v == self.robot.robot_id]:
                del self.local_reservations[k]

            # Lock peer positions for the full 30-tick horizon so Space-Time A* cannot just wait in place
            for p in self.peers.values():
                for dt in range(30):
                    self.local_reservations[(p.position[0], p.position[1], tick + dt)] = p.robot_id
                    self.local_reservations[(p.intended_pos[0], p.intended_pos[1], tick + dt)] = p.robot_id

            alt_path = self._timed_find_path(
                start=self.robot.position,
                goal=self.goal_pos,
                current_tick=tick,
                reservation_table=self.local_reservations,
                grid=self.grid,
            )
            peer_curr_positions = {p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id}
            detour_valid = False
            if alt_path and len(alt_path) > 1:
                next_step = (int(alt_path[1]["x"]), int(alt_path[1]["y"]))
                if next_step != self.robot.position and next_step not in peer_curr_positions:
                    self.robot.path = alt_path
                    reserve_path(alt_path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
                    intended_pos = next_step
                    detour_valid = True
                    self.log(f"[Tick {tick}] Alternate spatial detour found ({len(alt_path)} steps). Moving to {intended_pos}.")

            if not detour_valid:
                # Direct path hemmed in: step aside into adjacent free nook
                rx, ry = self.robot.position
                candidate_nooks = [(rx, ry - 1), (rx, ry + 1), (rx + 1, ry), (rx - 1, ry)]
                peer_positions = {p.position for p in self.peers.values()}
                peer_intents = {p.intended_pos for p in self.peers.values()}
                best_nook_cell = None
                for cand in candidate_nooks:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if self.grid.is_free(cand) and cand not in peer_positions and cand not in peer_intents:
                            best_nook_cell = cand
                            break
                if best_nook_cell:
                    intended_pos = best_nook_cell
                    self.robot.path = [
                        {"x": rx, "y": ry, "t": tick},
                        {"x": best_nook_cell[0], "y": best_nook_cell[1], "t": tick + 1}
                    ]
                    action_taken = "EVADED / STEPPED_ASIDE"
                    self.log(f"[Tick {tick}] Stepping aside into free nook {best_nook_cell} to break deadlock.")

        # 7. Broadcast Reservation Claim & Intention via Transport with HMAC
        self.seq += 1
        claim_payload = {
            "type": "RESERVATION_CLAIM",
            "robot_id": self.robot.robot_id,
            "robot_type": self.robot_type,
            "tick": tick,
            "position": [self.robot.position[0], self.robot.position[1]],
            "intended_pos": [intended_pos[0], intended_pos[1]],
            "heading": self.robot.heading.value,
            "priority_score": self.robot.priority_score,
            "state": self.fsm.state.value,
            "wait_ticks": self.robot.wait_ticks_so_far,
            "path": list(self.robot.path[:8]),
            "charger_target": list(self.charger_target) if self.charger_target else None,
        }
        envelope = sign_payload(claim_payload, secret_key=self.secret_key, seq=self.seq)
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                self.transport.send(peer_id, envelope)

        # 8. Drain inbox again for peer responses
        wait_start = time.time()
        max_peer_wait = max(0.08, self.tick_interval_s * 0.8)
        while (time.time() - wait_start) < max_peer_wait:
            self._drain_inbox(tick)
            nearby_stale = any(
                (abs(self.robot.position[0] - p.position[0]) + abs(self.robot.position[1] - p.position[1]) <= 3)
                and p.last_seen_tick < tick - 1
                for p in self.peers.values()
            )
            if not nearby_stale:
                break
            time.sleep(0.002)

        # Fail-safe check for unconfirmed immediately adjacent peer (packet actually dropped)
        unconfirmed_nearby = any(
            (abs(self.robot.position[0] - p.position[0]) + abs(self.robot.position[1] - p.position[1]) <= 2)
            and p.last_seen_tick < tick - 2
            for p in self.peers.values()
        )
        if unconfirmed_nearby and intended_pos != self.robot.position:
            intended_pos = self.robot.position
            action_taken = "WAITING"
            self.log(f"[Tick {tick}] HOLDING POSITION: Peer nearby dropped communication (>2 ticks).")

        # 9. Symmetric Conflict Detection & Arbitration
        action_taken = "MOVED"
        if self.fsm.state != RobotState.IDLE and intended_pos != self.robot.position:
            rx, ry = self.robot.position
            for peer_snap in list(self.peers.values()):
                if peer_snap.last_seen_tick < tick - 2:
                    continue

                px, py = peer_snap.position
                pix, piy = peer_snap.intended_pos
                m_dist = abs(rx - px) + abs(ry - py)
                if m_dist > 2:
                    continue

                is_swap = (intended_pos == (px, py) and (pix, piy) == (rx, ry) and (rx, ry) != (px, py))
                is_vertex = (intended_pos == (pix, piy) and intended_pos != (rx, ry))
                is_occupying = (intended_pos == (px, py))

                if is_swap or is_vertex or is_occupying:
                    c_type = "SWAP_CONFLICT" if is_swap else ("CELL_OVERLAP" if is_vertex else "STATIONARY_BLOCK")
                    conflict_cell = {"x": intended_pos[0], "y": intended_pos[1]}

                    my_score = float(self.robot.priority_score)
                    peer_score = float(peer_snap.priority_score)

                    if my_score > peer_score:
                        i_win = True
                    elif my_score < peer_score:
                        i_win = False
                    else:
                        i_win = (self.robot.robot_id < peer_snap.robot_id)

                    winner_id = self.robot.robot_id if i_win else peer_snap.robot_id
                    loser_id = peer_snap.robot_id if i_win else self.robot.robot_id

                    self.log(
                        f"[Tick {tick}] CONFLICT DETECTED with {peer_snap.robot_id} ({c_type}) at cell ({conflict_cell['x']}, {conflict_cell['y']})! "
                        f"My Priority={my_score:.1f}, Peer Priority={peer_score:.1f}"
                    )

                    if not i_win:
                        # THIS ROBOT IS THE LOSER -> YIELD RIGHT OF WAY
                        self.robot.wait_ticks_so_far += 1
                        self.consecutive_wait_ticks += 1

                        if self.fsm.state != RobotState.CONFLICT_NEGOTIATING:
                            self.pre_conflict_activity = self.fsm.state
                        self.fsm.transition(RobotEvent.CONFLICT_LOST)
                        self.robot.state = self.fsm.state

                        self.log(
                            f"[Tick {tick}] ARBITRATION RESULT: LOST to {peer_snap.robot_id}. Action=YIELD. Yielding right-of-way."
                        )

                        # Purge stale reservations for self
                        for k in [k for k, v in list(self.local_reservations.items()) if v == self.robot.robot_id]:
                            del self.local_reservations[k]

                        # 1. Lock winner's current cell and path into local_reservations for 30 ticks so A* cannot wait in place
                        for dt in range(30):
                            self.local_reservations[(px, py, tick + dt)] = peer_snap.robot_id
                        for p_step in peer_snap.path:
                            px_step = int(p_step["x"])
                            py_step = int(p_step["y"])
                            for dt in range(30):
                                self.local_reservations[(px_step, py_step, tick + dt)] = peer_snap.robot_id

                        # 2. Try to find a real spatial detour around the winner to goal
                        re_path = None
                        if self.goal_pos:
                            re_path = self._timed_find_path(
                                start=self.robot.position,
                                goal=self.goal_pos,
                                current_tick=tick,
                                reservation_table=self.local_reservations,
                                grid=self.grid,
                            )

                        detour_taken = False
                        if re_path and len(re_path) > 1:
                            next_detour = (int(re_path[1]["x"]), int(re_path[1]["y"]))
                            if next_detour != (rx, ry) and next_detour != (px, py) and next_detour != (pix, piy):
                                self.robot.path = re_path
                                intended_pos = next_detour
                                action_taken = "DETOUR_YIELD"
                                detour_taken = True
                                self.log(f"[Tick {tick}] Replanned alternate detour path ({len(re_path)} steps). Detouring to {intended_pos}.")

                        # 3. If no direct detour available, step aside into an adjacent free evasion cell (lateral nook or reverse)
                        if not detour_taken:
                            dx_rel = px - rx
                            dy_rel = py - ry
                            if dx_rel != 0:
                                candidate_evasions = [(rx, ry - 1), (rx, ry + 1), (rx - dx_rel, ry), (rx + dx_rel, ry)]
                            else:
                                candidate_evasions = [(rx - 1, ry), (rx + 1, ry), (rx, ry - dy_rel), (rx, ry + dy_rel)]

                            peer_blocked = {
                                (px, py), (pix, piy),
                                *((int(s["x"]), int(s["y"])) for s in peer_snap.path[:5])
                            }
                            other_peer_positions = {p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id}
                            evade_cell = None
                            for cand in candidate_evasions:
                                if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                                    if self.grid.is_free(cand) and cand not in peer_blocked and cand not in other_peer_positions:
                                        evade_cell = cand
                                        break

                            if evade_cell:
                                intended_pos = evade_cell
                                self.robot.path = [
                                    {"x": rx, "y": ry, "t": tick},
                                    {"x": evade_cell[0], "y": evade_cell[1], "t": tick + 1}
                                ]
                                action_taken = "EVADED / STEPPED_ASIDE"
                                self.log(f"[Tick {tick}] Stepping aside into evasion cell {evade_cell} to let {peer_snap.robot_id} pass.")
                            else:
                                intended_pos = self.robot.position
                                action_taken = "YIELDED / BRAKED"
                                self.robot.path = [
                                    {"x": rx, "y": ry, "t": tick},
                                    {"x": rx, "y": ry, "t": tick + 1}
                                ]
                                self.log(f"[Tick {tick}] Bottleneck hemmed in: holding position at {self.robot.position} for 1 tick.")

                        conflict_resolved = {
                            "winner_id": winner_id,
                            "loser_id": loser_id,
                            "action": action_taken,
                            "type": c_type,
                            "cell": conflict_cell,
                        }
                        break
                    else:
                        # THIS ROBOT IS THE WINNER -> PROCEED
                        self.log(
                            f"[Tick {tick}] ARBITRATION RESULT: WON against {peer_snap.robot_id}. Action=PROCEED."
                        )
                        # If winner's intended target cell is currently physically occupied by loser:
                        if intended_pos == (px, py):
                            if self.consecutive_wait_ticks < 2:
                                intended_pos = self.robot.position
                                action_taken = "WAITING"
                                self.log(f"[Tick {tick}] Pausing 1 tick at {self.robot.position} for yielding peer {peer_snap.robot_id} to clear {px, py}.")
                            else:
                                # Loser hasn't cleared after 2 ticks: winner replans around loser
                                self.log(f"[Tick {tick}] Peer {peer_snap.robot_id} unable to clear {px, py} after 2 ticks; winner seeking bypass.")
                                for dt in range(30):
                                    self.local_reservations[(px, py, tick + dt)] = peer_snap.robot_id
                                if self.goal_pos:
                                    w_bypass = self._timed_find_path(
                                        start=self.robot.position,
                                        goal=self.goal_pos,
                                        current_tick=tick,
                                        reservation_table=self.local_reservations,
                                        grid=self.grid,
                                    )
                                    if w_bypass and len(w_bypass) > 1:
                                        next_w = (int(w_bypass[1]["x"]), int(w_bypass[1]["y"]))
                                        if next_w != self.robot.position and next_w != (px, py):
                                            self.robot.path = w_bypass
                                            intended_pos = next_w
                                            action_taken = "DETOUR_WINNER"
                                            self.log(f"[Tick {tick}] Winner bypassing stationary peer to {next_w}.")

                        conflict_resolved = {
                            "winner_id": winner_id,
                            "loser_id": loser_id,
                            "action": action_taken if "DETOUR_WINNER" in action_taken else "PROCEED",
                            "type": c_type,
                            "cell": conflict_cell,
                        }
                        break

        # 10. Check Degraded Mode speed throttle
        if intended_pos != prev_pos and not self.degraded_detector.should_move_this_tick(tick):
            intended_pos = prev_pos
            action_taken = "DEGRADED_SPEED_PAUSE"
            self.log(f"[Tick {tick}] Degraded network throttle: pausing movement on alternate tick.")

        # 11. Check Task Realism Load Pause (every 4th step while carrying weight)
        if intended_pos != prev_pos and self.fsm.state == RobotState.EN_ROUTE_DROPOFF:
            p_weight = getattr(self.task, "payload_weight_kg", 0.0) if self.task else 0.0
            if p_weight > 0.0 and self.load_move_steps > 0 and (self.load_move_steps % 4 == 0):
                intended_pos = prev_pos
                action_taken = "LOAD_WEIGHT_PAUSE"
                self.load_move_steps += 1
                self.log(f"[Tick {tick}] Load weight inertia pause (carrying {p_weight}kg).")

        # 12. Commit Movement / Turn / Wait
        if intended_pos == prev_pos:
            if self.fsm.state == RobotState.IDLE or not self.robot.path:
                action_taken = "IDLE"
            else:
                self.consecutive_wait_ticks += 1
                if action_taken not in ("YIELDED / BRAKED", "LOAD_WEIGHT_PAUSE", "DEGRADED_SPEED_PAUSE"):
                    self.robot.wait_ticks_so_far += 1
                    action_taken = "WAITING"
                    self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.0)
        else:
            self.consecutive_wait_ticks = 0
            self.load_move_steps += 1
            dx = intended_pos[0] - prev_pos[0]
            dy = intended_pos[1] - prev_pos[1]
            if dx > 0: self.robot.heading = Heading.EAST
            elif dx < 0: self.robot.heading = Heading.WEST
            elif dy > 0: self.robot.heading = Heading.SOUTH
            elif dy < 0: self.robot.heading = Heading.NORTH

            if prev_heading != self.robot.heading and prev_pos == intended_pos:
                action_taken = "TURNED"
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.1)
            else:
                if action_taken not in ("VACATED_DOCK", "EVADED / STEPPED_ASIDE", "DETOUR_YIELD", "DETOUR_WINNER"):
                    action_taken = "MOVED"
                self.battery_move_steps += 1
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.2)

            self.robot.position = intended_pos
            self.robot.path = self.robot.path[1:] if len(self.robot.path) > 1 else []
            self.robot.wait_ticks_so_far = 0

            # Deterministic conflict resume if robot was negotiating
            if self.fsm.state == RobotState.CONFLICT_NEGOTIATING:
                if self.pre_conflict_activity == RobotState.EN_ROUTE_PICKUP:
                    self.fsm.transition(RobotEvent.RESUME_PICKUP)
                elif self.pre_conflict_activity == RobotState.EN_ROUTE_DROPOFF:
                    self.fsm.transition(RobotEvent.RESUME_DROPOFF)
                elif self.pre_conflict_activity == RobotState.AUDITING:
                    self.fsm.transition(RobotEvent.RESUME_AUDIT)
                else:
                    self.fsm.state = RobotState.FAILSAFE_HOLD
                self.pre_conflict_activity = None
                self.robot.state = self.fsm.state
                self.log(f"[Tick {tick}] Conflict cleared: resumed state {self.fsm.state.value}.")

        self.robot.last_updated_tick = tick

        # Check mission waypoint arrival
        if self.task:
            if self.fsm.state == RobotState.EN_ROUTE_PICKUP and self.robot.position == self.task.pickup:
                self.fsm.transition(RobotEvent.PICKUP_REACHED)
                self.robot.state = self.fsm.state
                self.log(f"[Tick {tick}] Arrived at pickup cell {self.task.pickup}! Entering PICKING state.")
            elif self.fsm.state == RobotState.EN_ROUTE_DROPOFF and self.robot.position == self.task.dropoff:
                self.fsm.transition(RobotEvent.DROPOFF_REACHED)
                self.robot.state = self.fsm.state
                self.log(f"[Tick {tick}] Arrived at dropoff cell {self.task.dropoff}! Entering DROPPING state.")
            elif self.fsm.state == RobotState.EN_ROUTE_PICKUP and self.robot.position == self.task.dropoff:
                # Direct route to dropoff or reached destination without distinct pickup
                self.fsm.transition(RobotEvent.PICKUP_REACHED)
                self.fsm.transition(RobotEvent.PICKUP_COMPLETE)
                self.fsm.transition(RobotEvent.DROPOFF_REACHED)
                self.robot.state = self.fsm.state
                self.log(f"[Tick {tick}] Reached mission destination {self.task.dropoff}! Entering DROPPING state.")
        elif self.charger_target and self.robot.position == self.charger_target:
            self.fsm.state = RobotState.CHARGING
            self.robot.state = self.fsm.state
            self.robot.path = []
            action_taken = "CHARGING"
            self.log(f"[Tick {tick}] Arrived at charger {self.charger_target}; charging.")
        elif self.fsm.state == RobotState.AUDITING and self.active_audit_mission:
            if self.robot.position == self.active_audit_mission.checkpoint:
                scan_msg = self.active_audit_mission.record_scan(self.robot.position)
                self.fsm.transition(RobotEvent.AUDIT_CHECKPOINT_LOGGED)
                self.robot.state = self.fsm.state
                self.active_audit_mission = None
                self.goal_pos = None
                self.robot.path = []
                action_taken = "COMPLETED"
                self.log(f"[Tick {tick}] {scan_msg}")
        elif (
            self.goal_pos is not None
            and self.task is not None
            and self.robot.position == self.goal_pos
            and self.fsm.state in (RobotState.EN_ROUTE_DROPOFF, RobotState.DROPPING)
        ):
            if self.fsm.state == RobotState.EN_ROUTE_DROPOFF:
                self.fsm.transition(RobotEvent.DROPOFF_REACHED)
            self.fsm.transition(RobotEvent.MISSION_COMPLETE)
            self.robot.state = self.fsm.state
            self.robot.path = []
            self.goal_pos = None
            if self.task:
                self.completed_task_ids.add(self.task.task_id)
                self.task.status = "COMPLETED"
                self.task = None
            action_taken = "COMPLETED"
            self.log(f"[Tick {tick}] REACHED DESTINATION {self.robot.position}! Mission COMPLETED.")

        prune_past(self.local_reservations, tick)

        self.log(
            f"[Tick {tick}] Pos={self.robot.position}, Heading={self.robot.heading.value}, "
            f"State={self.fsm.state.value}, Action={action_taken}, Priority={self.robot.priority_score:.1f}, "
            f"Battery={self.robot.battery_pct:.1f}%, Waits={self.robot.wait_ticks_so_far}"
        )

        return self._build_telemetry_frame(tick, action_taken, conflict_resolved)

    def _nearest_available_charger(self) -> Optional[Tuple[int, int]]:
        occupied = {
            peer.position for peer in self.peers.values()
            if peer.state == RobotState.CHARGING
        } | {
            peer.intended_pos for peer in self.peers.values()
            if peer.intended_pos in self.charging_stations
        } | {
            (p["x"], p["y"])
            for peer in self.peers.values()
            for p in (peer.path or [])
            if (p["x"], p["y"]) in self.charging_stations
        } | {
            peer.charger_target
            for peer in self.peers.values()
            if getattr(peer, "charger_target", None) is not None
        }
        candidates = [station for station in self.charging_stations if station not in occupied]
        if not candidates:
            return None
        return min(candidates, key=lambda station: (abs(station[0] - self.robot.position[0]) + abs(station[1] - self.robot.position[1]), station[0], station[1]))

    def _build_telemetry_frame(self, tick: int, action: str, conflict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        frame = {
            "tick": tick,
            "robot_id": self.robot.robot_id,
            "x": self.robot.position[0],
            "y": self.robot.position[1],
            "heading": self.robot.heading.value,
            "state": self.fsm.state.value,
            "battery_pct": round(self.robot.battery_pct, 1),
            "priority_score": round(self.robot.priority_score, 1),
            "urgency": self.urgency,
            "wait_ticks": self.robot.wait_ticks_so_far,
            "action": action,
            "completed": (self.fsm.state == RobotState.IDLE and not self.task),
            "current_task_id": self.task.task_id if self.task else None,
            "planner_latency_ms": round(self.last_planner_ms, 3),
            "path": [{"x": p["x"], "y": p["y"]} for p in self.robot.path[:8]],
            "goal": list(self.goal_pos) if self.goal_pos else list(self.robot.position),
            "conflict": conflict,
        }
        if self.telemetry_queue is not None:
            try:
                self.telemetry_queue.put_nowait(frame)
            except Exception:
                pass
        return frame

    def _drain_inbox(self, current_tick: int) -> None:
        """Drains incoming transport messages, verifies security envelopes, updates peer snapshots."""
        raw_messages = self.transport.recv_all()
        for msg in raw_messages:
            # Verify security envelope if present
            if "signature" in msg and "body" in msg:
                valid, payload, err = verify_envelope(msg, secret_key=self.secret_key)
                if not valid or not payload:
                    self.log(f"Security envelope verification failed: {err}")
                    continue
                body = msg.get("body", {})
                sender = payload.get("sender_id") or payload.get("robot_id", "unknown")
                seq = body.get("seq")
                ts = body.get("timestamp", time.time())
                r_valid, r_err = self.replay_guard.validate(sender, seq, ts)
                if not r_valid:
                    self.log(f"Security replay guard rejected packet from {sender}: {r_err}")
                    continue
                actual_msg = payload
            else:
                actual_msg = msg

            m_type = actual_msg.get("type")
            if m_type == "RESERVATION_CLAIM":
                sender_id = actual_msg["robot_id"]
                p_pos = tuple(actual_msg["position"])
                p_intent = tuple(actual_msg.get("intended_pos", actual_msg["position"]))
                h_val = actual_msg.get("heading", "NORTH")
                try:
                    h_enum = Heading(h_val)
                except Exception:
                    h_enum = Heading.NORTH
                s_val = actual_msg.get("state", "IDLE")
                try:
                    s_enum = RobotState(s_val)
                except Exception:
                    s_enum = RobotState.IDLE

                msg_tick = int(actual_msg["tick"])
                self.degraded_detector.record_peer_tick(sender_id, msg_tick)

                c_target = actual_msg.get("charger_target")
                charger_target_tuple = (int(c_target[0]), int(c_target[1])) if c_target else None

                snap = PeerSnapshot(
                    robot_id=sender_id,
                    position=(int(p_pos[0]), int(p_pos[1])),
                    intended_pos=(int(p_intent[0]), int(p_intent[1])),
                    heading=h_enum,
                    priority_score=float(actual_msg["priority_score"]),
                    state=s_enum,
                    wait_ticks_so_far=int(actual_msg["wait_ticks"]),
                    path=actual_msg["path"],
                    last_seen_tick=msg_tick,
                    charger_target=charger_target_tuple,
                )
                self.peers[sender_id] = snap

                # Update local reservation table
                for k in [k for k, v in list(self.local_reservations.items()) if v == sender_id]:
                    del self.local_reservations[k]
                for p in snap.path:
                    self.local_reservations[(int(p["x"]), int(p["y"]), int(p["t"]))] = sender_id

            elif m_type == "TASK_ASSIGNMENT":
                # Handle task assignment message from central dispatcher
                t_dict = actual_msg.get("task", {})
                tid = t_dict.get("task_id")
                if tid and tid not in self.completed_task_ids and self.fsm.state == RobotState.IDLE:
                    pickup_pos = tuple(t_dict["pickup"]) if "pickup" in t_dict else tuple(self.robot.position)
                    dropoff_pos = tuple(t_dict["dropoff"])
                    self._assign_initial_task(
                        goal_pos=dropoff_pos,
                        urgency=int(t_dict.get("urgency", 3)),
                        payload_weight_kg=float(t_dict.get("payload_weight_kg", 0.0)),
                        task_id=tid,
                        pickup_pos=pickup_pos,
                    )
                    self.log(f"[Tick {current_tick}] Accepted TASK_ASSIGNMENT {tid} to pickup {pickup_pos} -> dropoff {dropoff_pos}.")

            elif m_type == "EMERGENCY_STOP":
                self.log(f"[Tick {current_tick}] Control command received: EMERGENCY_STOP.")
                self.fsm.transition(RobotEvent.E_STOP)
                self.robot.state = self.fsm.state
                self.pre_conflict_activity = None

            elif m_type in ("RESET", "RESET_FAILSAFE"):
                self.log(f"[Tick {current_tick}] Control command received: {m_type}.")
                self.reset_failsafe()


def run_robot_process(
    robot_id: str,
    start_pos: Tuple[int, int],
    goal_pos: Tuple[int, int],
    urgency: int,
    battery_pct: float,
    obstacles: List[Tuple[int, int]],
    port: int,
    peer_ports: Dict[str, int],
    telemetry_queue: mp.Queue,
    stop_event: mp.Event,
    log_dir_str: str,
    tick_interval_s: float = 0.1,
    max_ticks: int = 100,
    charging_stations: Optional[Set[Tuple[int, int]]] = None,
    robot_type: str = "GOODS_TO_PERSON",
    enable_idle_audit: bool = True,
    pause_event: Optional[mp.Event] = None,
) -> None:
    """
    Process target function for an autonomous robot.
    Runs in its own independent process.
    """
    node = RobotNode(
        robot_id=robot_id,
        start_pos=start_pos,
        goal_pos=goal_pos,
        urgency=urgency,
        battery_pct=battery_pct,
        obstacles=obstacles,
        port=port,
        peer_ports=peer_ports,
        telemetry_queue=telemetry_queue,
        log_dir=Path(log_dir_str),
        tick_interval_s=tick_interval_s,
        charging_stations=charging_stations,
        robot_type=robot_type,
        enable_idle_audit=enable_idle_audit,
    )

    tick = 0
    try:
        while not stop_event.is_set() and (max_ticks <= 0 or tick < max_ticks):
            if pause_event is not None and pause_event.is_set():
                time.sleep(0.2)
                continue
            t0 = time.time()
            node.step(tick)
            tick += 1

            elapsed = time.time() - t0
            sleep_time = max(0.0, tick_interval_s - elapsed)
            time.sleep(sleep_time)
    finally:
        node.close()

    node.log(f"Robot Process terminated after {tick} ticks. Exiting.")
