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
from app.models.robot import AMRType
from app.models.task import Task, TaskStatus, TaskType
from app.models.robot_fsm import RobotEvent, RobotFSM, RobotState
from app.transport.base import Transport
from app.transport.udp_transport import UdpTransport
from app.transport.halow_transport import HaLowTransport
from app.security.hmac_envelope import (
    sign_payload,
    verify_envelope,
    build_inventory_update_envelope,
    build_resource_claim_envelope,
    build_resource_release_envelope,
    build_pod_occupancy_envelope,
)
from app.security.replay_guard import ReplayGuard
from app.services.degraded_mode import DegradedModeDetector
from app.services.audit_mission import AuditMission
from app.services.inventory_ledger import InventoryLedger
from app.models.inventory import ShelfRecord
from app.ml.priority_gnn import (
    ARBITRATION_RADIUS,
    DEFAULT_EMA_ALPHA,
    FALLBACK_COOLDOWN_TICKS,
    AntiFlappingCooldown,
    RightOfWayTracker,
    compute_priority,
)
from app.ml.priority_gnn_infer import get_priority_gnn_model
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
    robot_type: Optional[str] = None
    occupied_slot: Optional[Tuple[int, int]] = None


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
        enable_idle_audit: Optional[bool] = None,
        auto_idle_audit: Optional[bool] = None,
        auto_consolidation: Optional[bool] = None,
        auto_transfer: Optional[bool] = None,
        ledger: Optional[InventoryLedger] = None,
        fleet_roster: Optional[Dict[str, str]] = None,
        world: Optional[WorldConfig] = None,
        proximity_radius: Optional[float] = None,
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
        self.fleet_roster: Dict[str, str] = dict(fleet_roster or {})
        self.fleet_roster[self.robot_id] = robot_type
        self.host = host
        self.telemetry_queue = telemetry_queue
        self.tick_interval_s = tick_interval_s
        self.secret_key = secret_key
        default_world = world or build_default_world()
        self.world = default_world
        self.charging_stations = charging_stations or set(default_world.charging_stations)
        self.dropoff_stations: Set[Tuple[int, int]] = set(default_world.dropoff_stations)
        self.pickup_stations: Set[Tuple[int, int]] = set(default_world.pickup_stations)
        self.charger_target: Optional[Tuple[int, int]] = None
        self.active_claimed_pods: Set[str] = set()
        self.robot_type = robot_type

        # Step 1: Gate autonomous sources behind explicit flags, default OFF from config
        if auto_idle_audit is not None:
            self.auto_idle_audit = bool(auto_idle_audit)
        elif enable_idle_audit is not None:
            self.auto_idle_audit = bool(enable_idle_audit)
        else:
            self.auto_idle_audit = getattr(cfg, "AUTO_IDLE_AUDIT", False)
        self.enable_idle_audit = self.auto_idle_audit

        self.auto_consolidation = (
            bool(auto_consolidation)
            if auto_consolidation is not None
            else getattr(cfg, "AUTO_CONSOLIDATION", False)
        )
        self.auto_transfer = (
            bool(auto_transfer)
            if auto_transfer is not None
            else getattr(cfg, "AUTO_TRANSFER", False)
        )


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
        self.halow_transport = HaLowTransport(
            node_id=robot_id,
            dashboard_port=9099,
            host=host,
            packet_loss_pct=getattr(self.transport, "packet_loss_pct", 0.0),
        )

        # 3. Security & Replay Guard
        self.seq = 0
        self.replay_guard = ReplayGuard(freshness_window_s=5.0)

        # 4. Grid and Local Reservations
        grid_w = getattr(default_world, "width", 30) if default_world else 30
        grid_h = getattr(default_world, "height", 30) if default_world else 30
        self.grid = WarehouseGrid(obstacles=self.obstacles, width=grid_w, height=grid_h)
        if hasattr(default_world, "pod_slots") and default_world.pod_slots:
            self.grid.register_shelf_cells(default_world.pod_slots.values())
        self.local_reservations: Dict[Tuple[int, int, int], str] = {}
        self.HOLD = 30

        # 5. Deterministic Finite State Machine
        self.fsm = RobotFSM(RobotState.IDLE)
        self.pre_conflict_activity: Optional[RobotState] = None
        self.failsafe_hold_ticks = 0

        # 6. Mission & Task Management & Local Inventory Cache
        self.world = default_world
        from app.services.reservations import register_pod_slots
        if hasattr(self.world, "pod_slots") and self.world.pod_slots:
            register_pod_slots(self.world.pod_slots)
        self.inventory_ledger = ledger or InventoryLedger()
        self.local_inventory_cache: Dict[str, ShelfRecord] = {
            s.shelf_id: s for s in self.inventory_ledger.get_all_shelves()
        }
        self.task: Optional[Task] = None
        self.completed_task_ids: Set[str] = set()
        self.active_audit_mission: Optional[AuditMission] = None

        # 7. Robot Model Entity
        rt_enum = AMRType(robot_type) if isinstance(robot_type, str) else robot_type
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
            robot_type=rt_enum,
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

        # 11. Learned Edge Priority Model (NumPy Inference) with Anti-Flapping Cooldown & ROW Tracker
        self.gnn_model = get_priority_gnn_model()
        self.fallback_cooldown = AntiFlappingCooldown(cooldown_ticks=FALLBACK_COOLDOWN_TICKS)
        self.row_tracker = RightOfWayTracker(arbitration_radius=ARBITRATION_RADIUS)

        # 12. Contract-Net Decentralized Task Bidding & Lease Tracking
        self.active_bids: Dict[str, Dict[str, Any]] = {}
        self.known_task_claims: Set[str] = set()
        self.task_leases: Dict[str, Dict[str, Any]] = {}

        # 13. SORTING AMR Batch & Chute Occupancy Tracking
        self.carrying_batch: List[Dict[str, Any]] = []
        self.chute_occupancy: Dict[str, int] = {c_id: 0 for c_id in self.world.sortation_chutes}
        self.chute_full_threshold: int = 5

        # 14. Part C: Anticipatory charging parameters
        self.energy_per_cell: float = 0.25
        self.charging_safety_margin: float = 1.5
        self.charging_reserve_pct: float = 10.0

        # 15. Fixed Station Command Authorization & Rejection Audit
        self.rejected_station_commands: List[Dict[str, Any]] = []

        # 16. ARCH-01 Authority Station Heartbeat & Interim Coordinator
        self.authority_last_seen_tick: int = 0
        self.is_interim_coordinator: bool = False
        self.interim_coordinator_id: Optional[str] = None
        self.resolved_escalated_faults: List[Dict[str, Any]] = []

        # 17. NET-01 Proximity Filtering for Routine Broadcasts
        self.proximity_radius: Optional[float] = proximity_radius
        self.packets_sent_count: int = 0
        self.packets_filtered_count: int = 0

        # If goal_pos provided at startup, auto-initialize initial task for legacy/demo scenarios
        if self.goal_pos is not None:
            self._assign_initial_task(self.goal_pos, self.urgency)

    def trigger_autonomous_consolidation(
        self, chute_id: str, current_tick: int, force: bool = False
    ) -> Optional[Task]:
        """
        Autonomously spawns and broadcasts a CONSOLIDATE_EXPORT task when a sortation chute is full.
        Gated by self.auto_consolidation unless force=True (direct consequence of user task).
        """
        if not self.auto_consolidation and not force:
            return None
        chute_info = self.world.sortation_chutes.get(chute_id)
        if not chute_info:
            return None

        chute_pos = (chute_info["x"], chute_info["y"])
        gate_id = chute_info.get("gate_id", "OUT-2")
        export_dock_pos = self.world.gate_position(gate_id)

        consolidation_task_id = f"CONSOLIDATE-{chute_id}-{current_tick}"
        if consolidation_task_id in self.known_task_claims:
            return None

        self.log(f"[Tick {current_tick}] Chute {chute_id} reached full capacity ({self.chute_occupancy.get(chute_id, 0)} items). Autonomously triggering {consolidation_task_id}!")

        # Broadcast decentralized TASK_ANNOUNCEMENT to all peers
        self.seq += 1
        t_dict = {
            "task_id": consolidation_task_id,
            "task_type": "CONSOLIDATE_EXPORT",
            "pickup": list(chute_pos),
            "dropoff": list(export_dock_pos),
            "urgency": 5,
            "payload_weight_kg": float(self.chute_occupancy.get(chute_id, 5) * 2.0),
        }
        announcement_payload = {
            "type": "TASK_ANNOUNCEMENT",
            "sender_id": self.robot.robot_id,
            "task": t_dict,
            "tick": current_tick,
        }
        envelope = sign_payload(announcement_payload, secret_key=self.secret_key, seq=self.seq)
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                self.transport.send(peer_id, envelope)

        # Reset chute occupancy count once scheduled
        self.chute_occupancy[chute_id] = 0
        return Task(
            task_id=consolidation_task_id,
            pickup_x=chute_pos[0],
            pickup_y=chute_pos[1],
            dropoff_x=export_dock_pos[0],
            dropoff_y=export_dock_pos[1],
            urgency=5,
            created_tick=current_tick,
            task_type=TaskType.CONSOLIDATE_EXPORT,
        )

    def trigger_autonomous_transfer(
        self,
        pick_station_id: str,
        current_tick: int,
        carton: Optional[Any] = None,
        force: bool = False,
    ) -> Optional[Task]:
        """
        Part B: Autonomously announces a TRANSFER_TO_SORTATION task when a pick station
        has a carton waiting in its buffer.
        Gated by self.auto_transfer unless force=True (direct consequence of user task).
        """
        if not self.auto_transfer and not force:
            return None
        st_info = self.world.pick_stations.get(pick_station_id)
        if not st_info:
            return None

        pickup_pos = (st_info["x"], st_info["y"])
        entrances = self.world.sortation_zone.get("entrances", [(21, 3)])
        sort_entrance = entrances[0]

        transfer_task_id = f"TRANSFER-{pick_station_id}-{current_tick}"
        if transfer_task_id in self.known_task_claims:
            return None

        weight = getattr(carton, "weight_kg", 5.0) if carton else 5.0
        sku = getattr(carton, "sku", "SKU-ITEM") if carton else "SKU-ITEM"
        dest_zone = getattr(carton, "destination_zone", "ZONE_NORTH") if carton else "ZONE_NORTH"
        qty = getattr(carton, "qty", 1) if carton else 1

        self.log(f"[Tick {current_tick}] Carton available at {pick_station_id}. Autonomously triggering {transfer_task_id}!")

        # Broadcast decentralized TASK_ANNOUNCEMENT to all peers
        self.seq += 1
        t_dict = {
            "task_id": transfer_task_id,
            "task_type": "TRANSFER_TO_SORTATION",
            "pickup": list(pickup_pos),
            "dropoff": list(sort_entrance),
            "urgency": 4,
            "payload_weight_kg": weight,
            "sku_to_pick": sku,
            "destination_zone": dest_zone,
            "pick_station_id": pick_station_id,
            "quantity": qty,
        }
        announcement_payload = {
            "type": "TASK_ANNOUNCEMENT",
            "sender_id": self.robot.robot_id,
            "task": t_dict,
            "tick": current_tick,
        }
        envelope = sign_payload(announcement_payload, secret_key=self.secret_key, seq=self.seq)
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                self.transport.send(peer_id, envelope)

        return Task(
            task_id=transfer_task_id,
            pickup_x=pickup_pos[0],
            pickup_y=pickup_pos[1],
            dropoff_x=sort_entrance[0],
            dropoff_y=sort_entrance[1],
            urgency=4,
            created_tick=current_tick,
            payload_weight_kg=weight,
            task_type=TaskType.TRANSFER_TO_SORTATION,
            destination_zone=dest_zone,
        )

    def decant_batch_item(self, item: Dict[str, Any], tick: int) -> str:
        """
        Decants a single item into the appropriate sortation chute based on its destination zone.
        """
        dest_zone = item.get("destination_zone", "OVERFLOW")
        chute_id = self.world.chute_for_destination(dest_zone)
        self.chute_occupancy[chute_id] = self.chute_occupancy.get(chute_id, 0) + 1
        if hasattr(self, "world") and chute_id in getattr(self.world, "sortation_chutes", {}):
            self.world.sortation_chutes[chute_id]["current_count"] = self.world.sortation_chutes[chute_id].get("current_count", 0) + 1
        self.log(f"[Tick {tick}] SORTING Robot decanted item {item.get('item_id', 'ITEM')} (dest={dest_zone}) into {chute_id} (count={self.chute_occupancy[chute_id]}).")

        # Check full threshold
        if self.chute_occupancy[chute_id] >= self.chute_full_threshold:
            self.trigger_autonomous_consolidation(chute_id, tick, force=True)

        return chute_id

    def _timed_find_path(self, *args, **kwargs) -> List[Dict[str, Any]]:
        # Enforce shelf cells as impassable by default
        if "blocked_cells" not in kwargs:
            if hasattr(self, "grid") and getattr(self.grid, "_shelf_cells", None):
                kwargs["blocked_cells"] = set(self.grid._shelf_cells)

        if "robot_id" not in kwargs:
            kwargs["robot_id"] = getattr(self, "robot_id", None) or (self.robot.robot_id if hasattr(self, "robot") else None)

        if "start_heading" not in kwargs and hasattr(self, "robot") and getattr(self.robot, "heading", None):
            h_val = self.robot.heading
            kwargs["start_heading"] = h_val.value if hasattr(h_val, "value") else str(h_val)

        # Unladen G2P entering a designated shelf cell is the sole exception
        # ALSO allow laden G2P returning pod to its home slot
        if "allowed_exception" not in kwargs:
            goal = kwargs.get("goal") or (args[1] if len(args) > 1 else None)
            carrying = getattr(self.robot, "carrying_pod_id", None) if hasattr(self, "robot") else None
            is_g2p = (str(getattr(self, "robot_type", "")).endswith("GOODS_TO_PERSON"))
            home_slot = getattr(self.task, "home_slot", None) if hasattr(self, "task") and self.task else None
            returning_home = (
                carrying
                and is_g2p
                and hasattr(self, "task")
                and self.task
                and getattr(self.task, "return_to_home", False)
                and goal
                and home_slot
                and (tuple(goal) == tuple(home_slot))
            )
            if is_g2p and (not carrying or returning_home) and goal and hasattr(self, "grid") and self.grid.is_shelf_cell(goal):
                kwargs["allowed_exception"] = (int(goal[0]), int(goal[1]))
            else:
                kwargs["allowed_exception"] = None

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
            curr_t = getattr(self.robot, "last_updated_tick", 0) if hasattr(self, "robot") else 0
            for pid in list(getattr(self, "active_claimed_pods", [])):
                self.release_pod_resource(pid, curr_t)
            if getattr(self, "charger_target", None):
                self.release_charger_resource(self.charger_target, curr_t)
            from app.services.reservations import release_robot_pod_claims, release_robot_charger_claims
            release_robot_pod_claims(self.robot_id)
            release_robot_charger_claims(self.robot_id)
            if hasattr(self, "robot"):
                self.set_pod_slot_occupant(self.robot.position, None, tick=curr_t)
        except Exception:
            pass
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
        task_type: Any = "STANDARD",
        target_shelf_id: Optional[str] = None,
        sku_to_pick: Optional[str] = None,
        quantity: int = 1,
        destination_zone: Optional[str] = None,
        pick_station_id: Optional[str] = None,
    ) -> None:
        """Assigns an initial mission and plans the initial route."""
        tid = task_id or f"TASK-{self.robot_id}"
        p_pos = pickup_pos if pickup_pos is not None else self.start_pos
        from app.models.task import TaskType
        if isinstance(task_type, str):
            try:
                t_type_enum = TaskType(task_type)
            except Exception:
                t_type_enum = TaskType.STANDARD
        elif isinstance(task_type, TaskType):
            t_type_enum = task_type
        else:
            t_type_enum = TaskType.STANDARD

        # Part D: Pod-weight realism: auto-calculate weight if shelf specified
        if payload_weight_kg == 0.0 and target_shelf_id and getattr(self, "inventory_ledger", None):
            try:
                payload_weight_kg = self.inventory_ledger.get_shelf_weight_kg(target_shelf_id)
            except Exception:
                pass

        self.task = Task(
            task_id=tid,
            pickup_x=p_pos[0],
            pickup_y=p_pos[1],
            dropoff_x=goal_pos[0],
            dropoff_y=goal_pos[1],
            urgency=urgency,
            created_tick=0,
            assigned_robot_id=self.robot_id,
            status=TaskStatus.ASSIGNED,
            payload_weight_kg=payload_weight_kg,
            task_type=t_type_enum,
            target_shelf_id=target_shelf_id,
            sku_to_pick=sku_to_pick,
            quantity=quantity,
            destination_zone=destination_zone,
        )
        if pick_station_id:
            setattr(self.task, "pick_station_id", pick_station_id)
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

    def get_local_inventory(self, shelf_id: Optional[str] = None) -> Any:
        """Returns in-memory decentralized inventory cache without querying server."""
        if shelf_id:
            return self.local_inventory_cache.get(shelf_id)
        return dict(self.local_inventory_cache)

    def broadcast_inventory_update(
        self,
        shelf_id: str,
        current_tick: int,
        sku_manifest: Optional[Dict[str, int]] = None,
        box_count: Optional[int] = None,
        confidence: float = 1.0,
        is_audit: bool = True,
    ) -> None:
        """
        Broadcasts signed INVENTORY_UPDATE over peer mesh to GOODS_TO_PERSON / FETCH peers,
        and mirrors copy to DASHBOARD over HaLowTransport channel.
        """
        record = self.local_inventory_cache.get(shelf_id) or self.inventory_ledger.get_shelf(shelf_id)
        if record is None:
            # Fallback creation
            record = ShelfRecord(
                shelf_id=shelf_id,
                x=0,
                y=0,
                capacity_boxes=80,
                current_box_count=box_count or 0,
                sku_manifest=sku_manifest or {},
                last_audited_tick=current_tick,
                last_audited_by=self.robot.robot_id,
                confidence=confidence,
            )

        manifest = dict(sku_manifest) if sku_manifest is not None else dict(record.sku_manifest)
        total_boxes = box_count if box_count is not None else sum(manifest.values())
        conf = confidence if confidence is not None else record.confidence

        # Update local cache immediately
        record.sku_manifest = manifest
        record.current_box_count = total_boxes
        record.confidence = conf
        if is_audit:
            record.last_audited_tick = current_tick
            record.last_audited_by = self.robot.robot_id
            record.version = getattr(record, "version", 1) + 1
        self.local_inventory_cache[shelf_id] = record

        # 1. Peer Mesh UDP Broadcast: filtered only to GOODS_TO_PERSON peers (Phase 1.5 Fix 6)
        self.seq += 1
        mesh_env = build_inventory_update_envelope(
            shelf_id=shelf_id,
            x=record.x,
            y=record.y,
            sku_manifest=manifest,
            current_box_count=total_boxes,
            confidence=conf,
            tick=current_tick,
            source_robot_id=self.robot.robot_id,
            secret_key=self.secret_key,
            seq=self.seq,
            channel="MESH",
            version=getattr(record, "version", 1),
        )
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                peer_type = self.fleet_roster.get(peer_id)
                if peer_type is None and peer_id in self.peers:
                    peer_type = getattr(self.peers[peer_id], "robot_type", None)
                p_type_str = str(peer_type).upper() if peer_type else ""

                # If peer_type not explicitly found in roster or peer cache:
                if not p_type_str:
                    if peer_id in ("AMR-01", "AMR-02", "AMR-03", "AMR-04"):
                        p_type_str = "GOODS_TO_PERSON"
                    elif "G2P" in peer_id.upper() or "FETCH" in peer_id.upper():
                        p_type_str = "GOODS_TO_PERSON"
                    elif "SORT" in peer_id.upper():
                        p_type_str = "SORTING"
                    elif "AUDIT" in peer_id.upper() or "SCAN" in peer_id.upper():
                        p_type_str = "SCANNING_AUDIT"
                    elif not self.fleet_roster:
                        p_type_str = "GOODS_TO_PERSON"

                # FIX 6: only send the peer-mesh copy to peers whose robot_type == GOODS_TO_PERSON
                if "GOODS_TO_PERSON" in p_type_str:
                    self.transport.send(peer_id, mesh_env)

        # 2. Simulated WiFi HaLow Mirror Broadcast (DASHBOARD uplink)
        halow_env = build_inventory_update_envelope(
            shelf_id=shelf_id,
            x=record.x,
            y=record.y,
            sku_manifest=manifest,
            current_box_count=total_boxes,
            confidence=conf,
            tick=current_tick,
            source_robot_id=self.robot.robot_id,
            secret_key=self.secret_key,
            seq=self.seq,
            channel="HALOW",
            version=getattr(record, "version", 1),
        )
        self.halow_transport.send("DASHBOARD", halow_env)
        self.log(f"[Tick {current_tick}] Broadcasted INVENTORY_UPDATE for {shelf_id} (v={getattr(record, 'version', 1)}, count={total_boxes}) over Mesh + HaLow.")

    def broadcast_resource_claim(
        self,
        resource_type: str,
        resource_id: Any,
        current_tick: int,
        lease_ticks: int = 40,
    ) -> None:
        """Broadcasts signed RESOURCE_CLAIM envelope to all peer AMRs."""
        if not hasattr(self, "transport") or not hasattr(self, "peer_ports"):
            return
        self.seq += 1
        env = build_resource_claim_envelope(
            resource_type=resource_type,
            resource_id=resource_id,
            robot_id=self.robot.robot_id,
            tick=current_tick,
            lease_ticks=lease_ticks,
            priority_score=getattr(self.robot, "priority_score", 0.0),
            secret_key=self.secret_key,
            seq=self.seq,
        )
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                self.transport.send(peer_id, env)
                self.packets_sent_count += 1

    def broadcast_resource_release(
        self,
        resource_type: str,
        resource_id: Any,
        current_tick: int,
    ) -> None:
        """Broadcasts signed RESOURCE_RELEASE envelope to all peer AMRs."""
        if not hasattr(self, "transport") or not hasattr(self, "peer_ports"):
            return
        self.seq += 1
        env = build_resource_release_envelope(
            resource_type=resource_type,
            resource_id=resource_id,
            robot_id=self.robot.robot_id,
            tick=current_tick,
            secret_key=self.secret_key,
            seq=self.seq,
        )
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                self.transport.send(peer_id, env)
                self.packets_sent_count += 1

    def claim_pod_resource(
        self,
        shelf_id: str,
        current_tick: int,
        lease_ticks: int = 40,
    ) -> bool:
        """Atomically claims a pod locally and broadcasts the claim to all peer AMRs."""
        from app.services.reservations import claim_pod
        ok = claim_pod(shelf_id, self.robot.robot_id, current_tick=current_tick, lease_ticks=lease_ticks)
        if ok:
            self.active_claimed_pods.add(shelf_id)
            self.broadcast_resource_claim("POD", shelf_id, current_tick, lease_ticks)
        return ok

    def release_pod_resource(
        self,
        shelf_id: str,
        current_tick: int,
    ) -> None:
        """Releases a pod claim locally and broadcasts the release to all peer AMRs."""
        from app.services.reservations import release_pod
        release_pod(shelf_id, self.robot.robot_id)
        self.active_claimed_pods.discard(shelf_id)
        self.broadcast_resource_release("POD", shelf_id, current_tick)

    def claim_charger_resource(
        self,
        station_pos: Tuple[int, int],
        current_tick: int,
        lease_ticks: int = 40,
    ) -> bool:
        """Atomically claims a charging station locally and broadcasts the claim to all peer AMRs."""
        from app.services.reservations import claim_charger
        pos = (int(station_pos[0]), int(station_pos[1]))
        ok = claim_charger(pos, self.robot.robot_id, current_tick=current_tick, lease_ticks=lease_ticks)
        if ok:
            self.charger_target = pos
            self.broadcast_resource_claim("CHARGER", [pos[0], pos[1]], current_tick, lease_ticks)
        return ok

    def release_charger_resource(
        self,
        station_pos: Tuple[int, int],
        current_tick: int,
    ) -> None:
        """Releases a charging station claim locally and broadcasts the release to all peer AMRs."""
        from app.services.reservations import release_charger
        pos = (int(station_pos[0]), int(station_pos[1]))
        release_charger(pos, self.robot.robot_id)
        if self.charger_target == pos:
            self.charger_target = None
        self.broadcast_resource_release("CHARGER", [pos[0], pos[1]], current_tick)

    def _resolve_resource_contention(
        self,
        res_type: str,
        res_id: Any,
        peer_id: str,
        peer_priority: float,
        current_tick: int,
        lease_ticks: int = 40,
    ) -> bool:
        """
        Resolves contention when both self and peer_id claim the same resource.
        Returns True if self wins and retains claim, False if peer wins and self yields.
        """
        my_priority = getattr(self.robot, "priority_score", 0.0)
        peer_wins = (peer_priority > my_priority) or (
            peer_priority == my_priority and str(peer_id) < str(self.robot.robot_id)
        )
        if peer_wins:
            self.log(f"[Tick {current_tick}] Contention lost on {res_type} {res_id} to higher-priority {peer_id}. Yielding.")
            if res_type == "POD":
                s_id = str(res_id)
                self.active_claimed_pods.discard(s_id)
                if self.fsm.state in (RobotState.PICKING, RobotState.LIFTING, RobotState.EN_ROUTE_PICKUP):
                    if getattr(self.task, "target_shelf_id", None) == s_id:
                        self.fsm.state = RobotState.FAILSAFE_HOLD
                        self.robot.state = self.fsm.state
                from app.services.reservations import record_peer_pod_claim
                record_peer_pod_claim(s_id, peer_id, current_tick, lease_ticks)
            elif res_type == "CHARGER":
                c_pos = (int(res_id[0]), int(res_id[1]))
                if self.charger_target == c_pos:
                    self.charger_target = None
                from app.services.reservations import record_peer_charger_claim
                record_peer_charger_claim(c_pos, peer_id, current_tick, lease_ticks)
            return False
        else:
            self.log(f"[Tick {current_tick}] Contention won on {res_type} {res_id} against {peer_id}. Maintaining claim.")
            self.broadcast_resource_claim(res_type, res_id, current_tick, lease_ticks)
            return True

    def broadcast_pod_slot_occupancy(
        self,
        pos: Tuple[int, int],
        occupant: Optional[str],
        shelf_id: Optional[str] = None,
        tick: int = 0,
    ) -> None:
        """Broadcasts signed POD_SLOT_OCCUPANCY envelope to all peer AMRs."""
        if not hasattr(self, "transport") or not hasattr(self, "peer_ports"):
            return
        self.seq += 1
        env = build_pod_occupancy_envelope(
            pos=pos,
            occupant=occupant,
            shelf_id=shelf_id,
            tick=tick,
            source_robot_id=self.robot.robot_id,
            secret_key=self.secret_key,
            seq=self.seq,
        )
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                self.transport.send(peer_id, env)
                self.packets_sent_count += 1

    def set_pod_slot_occupant(
        self,
        pos: Tuple[int, int],
        occupant: Optional[str],
        shelf_id: Optional[str] = None,
        tick: int = 0,
    ) -> None:
        """Updates local grid pod-slot occupancy and broadcasts event to peer AMRs."""
        int_pos = (int(pos[0]), int(pos[1]))
        if hasattr(self, "grid") and hasattr(self.grid, "set_pod_slot_occupant"):
            self.grid.set_pod_slot_occupant(int_pos, occupant)
        self.broadcast_pod_slot_occupancy(int_pos, occupant, shelf_id=shelf_id, tick=tick)

    def step(self, tick: int) -> Dict[str, Any]:
        """
        Executes one autonomous tick loop step for this robot.
        """
        prev_pos = self.robot.position
        prev_heading = self.robot.heading
        intended_pos = self.robot.position
        action_taken = "IDLE"
        conflict_resolved = None
        intended_pos = self.robot.position

        # 0. Active GNN-Tuned Priority Calculation for this Tick with Mandatory Fallback
        dist_to_goal = 0
        if self.goal_pos:
            dist_to_goal = abs(self.robot.position[0] - self.goal_pos[0]) + abs(self.robot.position[1] - self.goal_pos[1])
        prev_p_score = getattr(self.robot, "priority_score", None)
        self.robot.priority_score = compute_priority(
            self.robot,
            self.task,
            dist_to_goal,
            gnn_model=self.gnn_model,
            prev_score=prev_p_score,
            alpha=DEFAULT_EMA_ALPHA,
            current_tick=tick,
            cooldown_tracker=self.fallback_cooldown,
        )

        # 1. Check Failsafe Watchdog
        if self.fsm.state == RobotState.FAILSAFE_HOLD:
            self.failsafe_hold_ticks += 1
            if self.failsafe_hold_ticks >= 5:
                self._recover_from_failsafe(tick=tick)
            else:
                self.log(f"[Tick {tick}] In FAILSAFE_HOLD ({self.failsafe_hold_ticks}/5 ticks). Holding position.")
                return self._build_telemetry_frame(tick, "HOLDING", None)

        # 2. Drain incoming transport messages & resolve contract-net bids
        self._drain_inbox(tick)
        self._update_interim_coordinator_election(tick)
        self._resolve_contract_net_bids(tick)

        # Phase 1.5 Fix 3: Flush pending HaLow outbound queue every tick
        if hasattr(self, "halow_transport") and self.halow_transport is not None:
            self.halow_transport.flush_pending()

        # Phase 1.5 & Part C: Prune stale pod & charger claims & renew active claims
        from app.services.reservations import (
            prune_stale_pod_claims, renew_pod_claim,
            prune_stale_charger_claims, renew_charger_claim
        )
        prune_stale_pod_claims(current_tick=tick)
        prune_stale_charger_claims(current_tick=tick)
        if self.charger_target:
            renew_charger_claim(self.charger_target, self.robot.robot_id, current_tick=tick)
            if tick % 2 == 0:
                self.broadcast_resource_claim("CHARGER", [self.charger_target[0], self.charger_target[1]], tick, lease_ticks=20)
        for p in list(self.active_claimed_pods):
            renew_pod_claim(p, self.robot.robot_id, current_tick=tick)
            if tick % 2 == 0:
                self.broadcast_resource_claim("POD", p, tick, lease_ticks=20)
        if self.robot.carrying_pod_id and self.robot.carrying_pod_id not in self.active_claimed_pods:
            renew_pod_claim(self.robot.carrying_pod_id, self.robot.robot_id, current_tick=tick)
        elif self.task and getattr(self.task, "target_shelf_id", None) and self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.LIFTING):
            renew_pod_claim(self.task.target_shelf_id, self.robot.robot_id, current_tick=tick)

        # Task Lease Renewal & Heartbeat
        if self.task and getattr(self.task, "task_id", None):
            tid = self.task.task_id
            self.task_leases[tid] = {"holder": self.robot.robot_id, "expires_tick": tick + 40}
            if tick % 5 == 0:
                self.seq += 1
                hb_payload = {
                    "type": "TASK_LEASE_HEARTBEAT",
                    "sender_id": self.robot.robot_id,
                    "robot_id": self.robot.robot_id,
                    "task_id": tid,
                    "tick": tick,
                    "lease_ticks": 40,
                }
                hb_env = sign_payload(hb_payload, secret_key=self.secret_key, seq=self.seq)
                for peer_id in self.peer_ports.keys():
                    if peer_id != self.robot.robot_id:
                        self.transport.send(peer_id, hb_env)
                if hasattr(self, "halow_transport") and self.halow_transport:
                    self.halow_transport.send("DASHBOARD", hb_env)

        # Check peer task lease expiries
        for tid, l_info in list(self.task_leases.items()):
            if l_info.get("holder") != self.robot.robot_id:
                if tick > l_info.get("expires_tick", 0) and tid not in self.completed_task_ids:
                    self.log(f"[Tick {tick}] Task lease expired for {tid} held by {l_info.get('holder')}. Clearing claim.")
                    self.known_task_claims.discard(tid)
                    self.active_bids.pop(tid, None)
                    self.task_leases.pop(tid, None)

        # 2.1. Periodic Anti-Entropy Gossip & Ledger Resync (Every 20 ticks)

        if tick % 20 == 0 and self.inventory_ledger is not None:
            for s in self.inventory_ledger.get_all_shelves(current_tick=tick):
                local = self.local_inventory_cache.get(s.shelf_id)
                if local is None:
                    self.local_inventory_cache[s.shelf_id] = s
                else:
                    curr_ver = getattr(local, "version", 1)
                    s_ver = getattr(s, "version", 1)
                    if (s_ver > curr_ver) or (s_ver == curr_ver and s.confidence > local.confidence) or (s_ver == curr_ver and s.confidence == local.confidence and s.last_audited_tick > local.last_audited_tick):
                        self.local_inventory_cache[s.shelf_id] = s

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
            peer_positions = {
                p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id
            }
            peer_intents = {
                p.intended_pos for p in self.peers.values() if p.robot_id != self.robot.robot_id
            }

            # If idle robot is sitting inside a dock bay, only vacate if an active robot is targeting it
            dock_targeted = any(
                (p.intended_pos == (rx, ry) or any(s.get("x") == rx and s.get("y") == ry for s in (p.path or [])))
                for p in self.peers.values()
                if p.robot_id != self.robot.robot_id and p.state != "IDLE"
            )
            if ((rx, ry) in self.dropoff_stations or (rx, ry) in self.pickup_stations) and dock_targeted:
                dock_incoming_path_cells = {
                    (int(s["x"]), int(s["y"]))
                    for p in self.peers.values()
                    if p.robot_id != self.robot.robot_id and p.state != "IDLE"
                    for s in (p.path or [])
                }
                clear_candidates = [(rx - 1, ry), (rx + 1, ry), (rx, ry - 1), (rx, ry + 1)]
                for cand in clear_candidates:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if (
                            self.grid.is_free(cand)
                            and cand not in peer_positions
                            and cand not in peer_intents
                            and cand not in dock_incoming_path_cells
                            and cand not in self.dropoff_stations
                            and cand not in self.pickup_stations
                        ):
                            intended_pos = cand
                            action_taken = "VACATED_DOCK"
                            self.robot.path = [{"x": rx, "y": ry, "t": tick}, {"x": cand[0], "y": cand[1], "t": tick + 1}]
                            self.log(f"[Tick {tick}] Vacating active dock bay ({rx}, {ry}) -> {cand} to keep transfer zone clear.")
                            break

            # If an active working robot is targeting our cell, approaching within 2 cells, or our cell is in its planned path
            peer_targeting_us = any(
                (
                    p.intended_pos == (rx, ry)
                    or (abs(p.position[0] - rx) + abs(p.position[1] - ry) <= 2 and any(step.get("x") == rx and step.get("y") == ry for step in (p.path or [])))
                )
                for p in self.peers.values()
                if p.robot_id != self.robot.robot_id and p.state != "IDLE"
            )
            if peer_targeting_us and intended_pos == self.robot.position:
                oncoming_peers = [
                    p for p in self.peers.values()
                    if p.robot_id != self.robot.robot_id and p.state != "IDLE" and
                    (p.intended_pos == (rx, ry) or any(s.get("x") == rx and s.get("y") == ry for s in (p.path or [])))
                ]
                peer_path_cells = {
                    (int(s["x"]), int(s["y"]))
                    for p in oncoming_peers
                    for s in (p.path or [])
                } | {p.intended_pos for p in oncoming_peers} | {p.position for p in oncoming_peers}

                dx_app = oncoming_peers[0].position[0] - rx if oncoming_peers else 0
                if dx_app != 0:
                    evade_candidates = [(rx, ry - 1), (rx, ry + 1), (rx - dx_app, ry), (rx + dx_app, ry)]
                else:
                    evade_candidates = [(rx + 1, ry), (rx - 1, ry), (rx, ry - 1), (rx, ry + 1)]

                for cand in evade_candidates:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if (
                            self.grid.is_free(cand)
                            and cand not in peer_positions
                            and cand not in peer_intents
                            and cand not in peer_path_cells
                        ):
                            intended_pos = cand
                            action_taken = "EVADED / STEPPED_ASIDE"
                            self.robot.path = [{"x": rx, "y": ry, "t": tick}, {"x": cand[0], "y": cand[1], "t": tick + 1}]
                            self.log(f"[Tick {tick}] Idle node proactively stepping aside from ({rx}, {ry}) to {cand} for oncoming active peer.")
                            break

        # Check battery threshold & charging
        if self.fsm.state == RobotState.CHARGING:
            self.robot.battery_pct = min(100.0, self.robot.battery_pct + 4.0)
            if self.charger_target:
                from app.services.reservations import renew_charger_claim
                renew_charger_claim(self.charger_target, self.robot.robot_id, tick)
            if self.robot.battery_pct >= 95.0:
                if self.charger_target:
                    self.release_charger_resource(self.charger_target, tick)
                self.fsm.transition(RobotEvent.CHARGE_COMPLETE)
                self.robot.state = self.fsm.state
                self.charger_target = None
                self.goal_pos = None
                self.robot.path = []
                self.log(f"[Tick {tick}] Charging complete ({self.robot.battery_pct:.1f}%). Returning to IDLE.")
            return self._build_telemetry_frame(tick, "CHARGING", None)

        if self.fsm.state != RobotState.CHARGING:
            from app.services.reservations import prune_stale_charger_claims, renew_charger_claim
            prune_stale_charger_claims(tick)

            # If already en route to a charger, keep claim renewed
            if self.charger_target is not None:
                renew_charger_claim(self.charger_target, self.robot.robot_id, tick)
            else:
                # Dynamic anticipatory charging check (Part C)
                # Formula: required_pct = (dist * energy_per_cell * safety_margin) + reserve_pct
                should_charge = False
                if self.charging_stations:
                    station_candidates = sorted(
                        self.charging_stations,
                        key=lambda s: abs(s[0] - self.robot.position[0]) + abs(s[1] - self.robot.position[1])
                    )
                    nearest_s = station_candidates[0]
                    dist_to_nearest = abs(nearest_s[0] - self.robot.position[0]) + abs(nearest_s[1] - self.robot.position[1])
                    dyn_threshold = (dist_to_nearest * self.energy_per_cell * self.charging_safety_margin) + self.charging_reserve_pct

                    if self.robot.battery_pct <= dyn_threshold or self.robot.battery_pct < 20.0:
                        should_charge = True

                if should_charge:
                    target_station = self._nearest_available_charger(current_tick=tick, filter_reachable=True, claim=True)
                    if target_station is None:
                        # Fallback without reachable filter if battery is low
                        target_station = self._nearest_available_charger(current_tick=tick, filter_reachable=False, claim=True)

                    if target_station is not None:
                        self.charger_target = target_station
                        self.goal_pos = target_station
                        if self.robot.position == target_station:
                            self.fsm.state = RobotState.CHARGING
                            self.robot.state = self.fsm.state
                            self.robot.path = []
                            self.log(f"[Tick {tick}] At charger station {self.charger_target}; charging.")
                            return self._build_telemetry_frame(tick, "CHARGING", None)

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
                    else:
                        self.log(f"[Tick {tick}] All charging stations occupied or contested; holding at {self.robot.position}.")
                        return self._build_telemetry_frame(tick, "CHARGER_QUEUE_WAIT", None)

        # Check idle background audit patrol trigger (SCANNING_AUDIT robots, or legacy test robots)
        is_audit_eligible = (
            self.robot_type == "SCANNING_AUDIT"
            or (not self.robot_id.startswith("AMR-G2P") and not self.robot_id.startswith("AMR-SORT") and self.robot_type != "SORTING")
        )
        if self.auto_idle_audit and is_audit_eligible and self.fsm.state == RobotState.IDLE and not self.task:
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

        # 3. Handle Atomic PICKING / DROPPING / LIFTING / LOWERING ticks
        if self.fsm.state in (RobotState.PICKING, RobotState.LIFTING):
            self.log(f"[Tick {tick}] Executing pickup/lift at {self.robot.position}...")

            # If G2P robot, lift shelf pod
            if self.robot_type == "GOODS_TO_PERSON":
                shelf_id = getattr(self.task, "target_shelf_id", None) or self.world.shelf_at(self.robot.position[0], self.robot.position[1])
                if not shelf_id:
                    nearby = [sid for sid, pos in self.world.pod_slots.items() if abs(pos[0] - self.robot.position[0]) + abs(pos[1] - self.robot.position[1]) <= 1]
                    if nearby:
                        shelf_id = nearby[0]
                if shelf_id:
                    # Atomically claim shelf before lifting and broadcast to peers
                    if not self.claim_pod_resource(shelf_id, tick):
                        self.log(f"[Tick {tick}] POD CLAIM REJECTED: Shelf {shelf_id} is already claimed by another robot. Transitioning to FAILSAFE_HOLD.")
                        self.fsm.state = RobotState.FAILSAFE_HOLD
                        self.robot.state = self.fsm.state
                        return self._build_telemetry_frame(tick, "POD_CLAIM_REJECTED", None)

                    self.robot.carrying_pod_id = shelf_id
                    self.set_pod_slot_occupant(self.robot.position, self.robot.robot_id, shelf_id=shelf_id, tick=tick)
                    shelf_rec = self.local_inventory_cache.get(shelf_id) or self.inventory_ledger.get_shelf(shelf_id)
                    if shelf_rec:
                        self.robot.carrying_sku_manifest = dict(shelf_rec.sku_manifest)
                    self.log(f"[Tick {tick}] G2P Robot lifted pod {shelf_id} at {self.robot.position}.")

            # If SORTING robot executing TRANSFER_TO_SORTATION, take carton from pick station
            if self.robot_type == "SORTING" and self.task and getattr(self.task, "task_type", None) in (TaskType.TRANSFER_TO_SORTATION, "TRANSFER_TO_SORTATION"):
                ps_id = getattr(self.task, "pick_station_id", None) or self.world.pick_station_at(self.robot.position[0], self.robot.position[1])
                if not ps_id:
                    nearby_stations = [
                        sid for sid, s in self.world.pick_stations.items()
                        if abs(s["x"] - self.robot.position[0]) + abs(s["y"] - self.robot.position[1]) <= 1
                    ]
                    ps_id = nearby_stations[0] if nearby_stations else "PICK-01"
                carton = self.world.take_carton_from_pick_station(ps_id)
                self.log(f"[Tick {tick}] SORTING Robot picked up carton from {ps_id} buffer (carton={carton}).")

            if self.fsm.can_transition(RobotEvent.LIFT_COMPLETE):
                self.fsm.transition(RobotEvent.LIFT_COMPLETE)
            else:
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
            self.robot.state = self.fsm.state
            # Broadcast newly planned dropoff route so peers immediately see the reservation
            self.seq += 1
            claimed_pod = self.robot.carrying_pod_id or (getattr(self.task, "target_shelf_id", None) if self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.PICKING, RobotState.LIFTING) else None)
            claim_payload = {
                "type": "RESERVATION_CLAIM",
                "robot_id": self.robot.robot_id,
                "robot_type": self.robot_type,
                "tick": tick,
                "position": [self.robot.position[0], self.robot.position[1]],
                "intended_pos": [self.robot.position[0], self.robot.position[1]],
                "heading": self.robot.heading.value,
                "priority_score": self.robot.priority_score,
                "state": self.fsm.state.value,
                "wait_ticks": self.robot.wait_ticks_so_far,
                "path": list(self.robot.path[:8]) if self.robot.path else [{"x": self.robot.position[0], "y": self.robot.position[1], "t": tick + dt} for dt in range(8)],
                "charger_target": list(self.charger_target) if self.charger_target else None,
                "claimed_pod_id": claimed_pod,
                "active_claimed_pods": list(self.active_claimed_pods),
                "occupied_slot": [self.robot.position[0], self.robot.position[1]] if (self.robot.carrying_pod_id or self.fsm.state in (RobotState.PICKING, RobotState.LIFTING)) else None,
            }
            envelope = sign_payload(claim_payload, secret_key=self.secret_key, seq=self.seq)
            for peer_id in self.peer_ports.keys():
                if peer_id != self.robot.robot_id:
                    if self.is_peer_in_proximity(peer_id):
                        self.transport.send(peer_id, envelope)
                        self.packets_sent_count += 1
                    else:
                        self.packets_filtered_count += 1
            return self._build_telemetry_frame(tick, "LIFTING_COMPLETE", None)

        if self.fsm.state in (RobotState.DROPPING, RobotState.LOWERING):
            self.log(f"[Tick {tick}] Executing dropoff/lowering at {self.robot.position}...")

            # If carrying pod, handle item pick decrement and lower pod
            if self.robot.carrying_pod_id:
                pod_id = self.robot.carrying_pod_id
                sku_to_pick = getattr(self.task, "sku_to_pick", None) if self.task else None
                qty = getattr(self.task, "quantity", 1) if self.task else 1
                t_type = getattr(self.task, "task_type", None)

                # Part B: G2P item pick creates Carton and deposits to pick-station buffer with backpressure
                is_item_pick = (t_type in (TaskType.PICK_ITEM, "PICK_ITEM")) or (
                    self.robot_type == "GOODS_TO_PERSON" and self.task and self.task.dropoff in [
                        (s["x"], s["y"]) for s in self.world.pick_stations.values()
                    ]
                )
                if is_item_pick:
                    from app.models.carton import Carton
                    from app.services.inventory_ledger import DEFAULT_BOX_WEIGHT_KG
                    carton_weight = float(qty) * DEFAULT_BOX_WEIGHT_KG
                    dest_zone = getattr(self.task, "destination_zone", "ZONE_NORTH")

                    pref_station = self.world.pick_station_at(self.robot.position[0], self.robot.position[1])
                    if not pref_station:
                        nearby_stations = [
                            sid for sid, s in self.world.pick_stations.items()
                            if abs(s["x"] - self.robot.position[0]) + abs(s["y"] - self.robot.position[1]) <= 1
                        ]
                        pref_station = nearby_stations[0] if nearby_stations else None

                    carton = Carton(
                        sku=sku_to_pick or "SKU-ITEM",
                        qty=qty,
                        destination_zone=dest_zone,
                        source_shelf_id=pod_id,
                        created_tick=tick,
                        weight_kg=carton_weight,
                    )
                    deposited_st = self.world.deposit_carton_to_pick_station(carton, preferred_station_id=pref_station)
                    if deposited_st is None:
                        # Pick station buffer is saturated: hold with backpressure wait
                        self.log(f"[Tick {tick}] Pick station buffer is FULL. Backpressure hold at {self.robot.position}.")
                        return self._build_telemetry_frame(tick, "BUFFER_FULL_WAIT", None)

                    self.log(f"[Tick {tick}] Deposited carton ({carton.sku} x{carton.qty}, {carton.weight_kg}kg) into {deposited_st} buffer.")
                    self.trigger_autonomous_transfer(deposited_st, tick, carton, force=True)

                home_slot = getattr(self.task, "home_slot", None) or (self.task.pickup if self.task else None)
                is_at_home = bool(home_slot and self.robot.position == home_slot)

                if not is_at_home and not getattr(self.task, "_pick_executed", False):
                    shelf_rec = self.local_inventory_cache.get(pod_id) or self.inventory_ledger.get_shelf(pod_id)
                    if shelf_rec:
                        current_manifest = dict(shelf_rec.sku_manifest)
                        if sku_to_pick and sku_to_pick in current_manifest:
                            picked_sku = sku_to_pick
                        elif current_manifest:
                            picked_sku = max(current_manifest.keys(), key=lambda k: current_manifest[k])
                        else:
                            picked_sku = "DEFAULT_SKU"

                        # Phase 1.5 Fix 5: Decrement locally and use record_pick to preserve audit timestamp and confidence
                        current_manifest[picked_sku] = max(0, current_manifest.get(picked_sku, 0) - qty)
                        new_manifest = current_manifest
                        new_box_count = sum(new_manifest.values())
                        preserved_conf = shelf_rec.confidence

                        try:
                            self.inventory_ledger.record_pick(
                                shelf_id=pod_id,
                                sku=picked_sku,
                                qty_delta=qty,
                                robot_id=self.robot.robot_id,
                                tick=tick,
                                manifest_override=new_manifest,
                            )
                        except Exception as e:
                            self.log(f"[Tick {tick}] Ledger pick recording failed (server down/offline): {e}")

                        self.broadcast_inventory_update(
                            shelf_id=pod_id,
                            current_tick=tick,
                            sku_manifest=new_manifest,
                            box_count=new_box_count,
                            confidence=preserved_conf,
                            is_audit=False,
                        )
                        self.log(f"[Tick {tick}] Pick operation completed on pod {pod_id} (picked {qty} units of {picked_sku}).")
                    if self.task:
                        self.task._pick_executed = True

                should_return_home = bool(getattr(self.task, "return_to_home", False))

                if should_return_home and home_slot and self.robot.position != home_slot:
                    self.log(f"[Tick {tick}] G2P Shelf Pick finished. Returning shelf {pod_id} to home slot {home_slot}...")
                    self.goal_pos = home_slot
                    # Update task dropoff so arrival detection works
                    self.task.dropoff_x = home_slot[0]
                    self.task.dropoff_y = home_slot[1]
                    # Plan path to home slot
                    p_home = self._timed_find_path(
                        start=self.robot.position,
                        goal=home_slot,
                        current_tick=tick,
                        reservation_table=self.local_reservations,
                        grid=self.grid,
                    )
                    if p_home and len(p_home) > 1:
                        self.robot.path = p_home
                        reserve_path(p_home, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
                    else:
                        self.robot.path = []
                    self.fsm.state = RobotState.EN_ROUTE_DROPOFF
                    self.robot.state = RobotState.EN_ROUTE_DROPOFF
                    return self._build_telemetry_frame(tick, "RETURNING_SHELF_TO_HOME", None)

                # Release pod claim and broadcast release to peers
                self.release_pod_resource(pod_id, tick)
                self.set_pod_slot_occupant(self.robot.position, None, shelf_id=pod_id, tick=tick)

                self.robot.carrying_pod_id = None
                self.robot.carrying_sku_manifest = {}

            # Handle SORTING AMR completing TRANSFER_TO_SORTATION
            if self.task and getattr(self.task, "task_type", None) in (TaskType.TRANSFER_TO_SORTATION, "TRANSFER_TO_SORTATION"):
                dest_zone = getattr(self.task, "destination_zone", "ZONE_NORTH")
                sku = getattr(self.task, "sku_to_pick", "ITEM")
                decant_item = {"item_id": sku, "destination_zone": dest_zone}
                chute_id = self.decant_batch_item(decant_item, tick)
                self.log(f"[Tick {tick}] SORTING Robot transferred carton to sortation zone and decanted to chute {chute_id}.")

            if self.task:
                completed_tid = self.task.task_id
                self.completed_task_ids.add(completed_tid)
                self.task_leases.pop(completed_tid, None)
                self.known_task_claims.add(completed_tid)
                self.task.status = "COMPLETED"

                # Broadcast signed TASK_COMPLETED
                self.seq += 1
                comp_payload = {
                    "type": "TASK_COMPLETED",
                    "sender_id": self.robot.robot_id,
                    "robot_id": self.robot.robot_id,
                    "task_id": completed_tid,
                    "tick": tick,
                }
                comp_env = sign_payload(comp_payload, secret_key=self.secret_key, seq=self.seq)
                for peer_id in self.peer_ports.keys():
                    if peer_id != self.robot.robot_id:
                        self.transport.send(peer_id, comp_env)
                if hasattr(self, "halow_transport") and self.halow_transport:
                    self.halow_transport.send("DASHBOARD", comp_env)

                self.task = None
            self.goal_pos = None
            self.robot.path = []
            self.load_move_steps = 0
            if self.fsm.can_transition(RobotEvent.LOWER_COMPLETE):
                self.fsm.transition(RobotEvent.LOWER_COMPLETE)
            else:
                self.fsm.transition(RobotEvent.MISSION_COMPLETE)
            self.robot.state = self.fsm.state
            self.pre_conflict_activity = None
            action_taken = "MISSION_COMPLETED"


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
        from app.services.policies.stop_and_wait import is_stop_and_wait_enabled, StopAndWaitPolicy
        deadlock_threshold = 20 if is_stop_and_wait_enabled() else 3
        if (
            self.consecutive_wait_ticks >= deadlock_threshold
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
                for s in (p.path or [])[:4]:
                    self.local_reservations[(int(s["x"]), int(s["y"]), tick + 1)] = p.robot_id
                    self.local_reservations[(int(s["x"]), int(s["y"]), tick + 2)] = p.robot_id

            alt_path = self._timed_find_path(
                start=self.robot.position,
                goal=self.goal_pos,
                current_tick=tick,
                reservation_table=self.local_reservations,
                grid=self.grid,
            )
            peer_curr_positions = {p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id}
            peer_curr_intents = {p.intended_pos for p in self.peers.values() if p.robot_id != self.robot.robot_id}
            for p in self.peers.values():
                if p.robot_id != self.robot.robot_id:
                    for s in (p.path or [])[:3]:
                        peer_curr_intents.add((int(s["x"]), int(s["y"])))
            detour_valid = False
            if alt_path and len(alt_path) > 1:
                next_step = (int(alt_path[1]["x"]), int(alt_path[1]["y"]))
                is_turn = (next_step == self.robot.position and len(alt_path) > 2)
                if (next_step != self.robot.position or is_turn) and next_step not in peer_curr_positions and next_step not in peer_curr_intents:
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
                peer_path_cells = {
                    (int(s["x"]), int(s["y"]))
                    for p in self.peers.values()
                    if p.robot_id != self.robot.robot_id
                    for s in p.path[:3]
                }
                best_nook_cell = None
                for cand in candidate_nooks:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if (
                            self.grid.is_free(cand)
                            and not self.grid.is_shelf_cell(cand)
                            and cand not in peer_positions
                            and cand not in peer_intents
                            and cand not in peer_path_cells
                        ):
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
        claimed_pod = self.robot.carrying_pod_id or (getattr(self.task, "target_shelf_id", None) if self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.PICKING, RobotState.LIFTING) else None)
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
            "path": list(self.robot.path[:8]) if self.robot.path else [{"x": self.robot.position[0], "y": self.robot.position[1], "t": tick + dt} for dt in range(8)],
            "charger_target": list(self.charger_target) if self.charger_target else None,
            "claimed_pod_id": claimed_pod,
            "active_claimed_pods": list(self.active_claimed_pods),
            "occupied_slot": [self.robot.position[0], self.robot.position[1]] if (self.robot.carrying_pod_id or self.fsm.state in (RobotState.PICKING, RobotState.LIFTING)) else None,
        }
        envelope = sign_payload(claim_payload, secret_key=self.secret_key, seq=self.seq)
        for peer_id in self.peer_ports.keys():
            if peer_id != self.robot.robot_id:
                if self.is_peer_in_proximity(peer_id):
                    self.transport.send(peer_id, envelope)
                    self.packets_sent_count += 1
                else:
                    self.packets_filtered_count += 1

        # 8. Drain inbox again for peer responses (Synchronize claims with immediate collision vicinity)
        wait_start = time.time()
        max_peer_wait = 0.0 if self.tick_interval_s <= 0.0 else min(0.015, self.tick_interval_s * 0.45)
        while (time.time() - wait_start) < max_peer_wait:
            self._drain_inbox(tick)
            rx, ry = self.robot.position
            ix, iy = intended_pos
            nearby_stale = any(
                (
                    min(
                        abs(rx - p.position[0]) + abs(ry - p.position[1]),
                        abs(ix - p.position[0]) + abs(iy - p.position[1]),
                        abs(rx - p.intended_pos[0]) + abs(ry - p.intended_pos[1]),
                        abs(ix - p.intended_pos[0]) + abs(iy - p.intended_pos[1]),
                    ) <= 5
                )
                and p.last_seen_tick < tick
                for p in self.peers.values()
                if p.robot_id != self.robot.robot_id
            )
            if not nearby_stale:
                break
            time.sleep(0.0005)

        # Fail-safe check for unconfirmed peer targeting our intended cell
        if intended_pos != self.robot.position:
            unconfirmed_collision = any(
                (
                    intended_pos == p.position
                    or intended_pos == p.intended_pos
                )
                and p.last_seen_tick < tick
                for p in self.peers.values()
                if p.robot_id != self.robot.robot_id
            )
            if unconfirmed_collision:
                intended_pos = self.robot.position
                action_taken = "WAITING"
                self.log(f"[Tick {tick}] HOLDING POSITION: Peer nearby unconfirmed at tick {tick}.")

        # 9. Symmetric Conflict Detection & Arbitration (or Stop-and-Wait Policy)
        from app.services.policies.stop_and_wait import is_stop_and_wait_enabled, StopAndWaitPolicy

        action_taken = "MOVED"
        if is_stop_and_wait_enabled():
            # Traditional Stop-and-Wait Baseline: No arbitration, no peer negotiation.
            # If intended next cell is occupied by any peer, halt in place.
            # In textbook stop-and-wait, a robot halts if the target cell is currently occupied
            # by any peer, or if another peer has arrived or is entering that cell.
            blocked_cells = set()
            for p in self.peers.values():
                if p.robot_id != self.robot.robot_id and p.last_seen_tick >= tick - 4:
                    blocked_cells.add(p.position)
                    blocked_cells.add(p.intended_pos)
            intended_pos, action_taken, did_halt = StopAndWaitPolicy.check_halt(
                self.robot.position, intended_pos, blocked_cells
            )
            if did_halt:
                self.robot.wait_ticks_so_far += 1
                self.log(f"[Tick {tick}] STOP_AND_WAIT: Target cell occupied. Halting in place at {self.robot.position}.")
        elif intended_pos != self.robot.position:
            rx, ry = self.robot.position
            for peer_snap in list(self.peers.values()):
                if peer_snap.last_seen_tick < tick - 5 and intended_pos != peer_snap.position:
                    continue

                px, py = peer_snap.position
                pix, piy = peer_snap.intended_pos

                m_dist = min(
                    abs(rx - px) + abs(ry - py),
                    abs(rx - pix) + abs(ry - piy),
                    abs(intended_pos[0] - px) + abs(intended_pos[1] - py),
                    abs(intended_pos[0] - pix) + abs(intended_pos[1] - piy),
                )
                if m_dist > 2:
                    continue

                is_swap = (intended_pos == (px, py) and (pix, piy) == (rx, ry) and (rx, ry) != (px, py))
                is_vertex = (intended_pos == (pix, piy) and intended_pos != (rx, ry))
                is_occupying = (intended_pos == (px, py))

                if is_swap or is_vertex or is_occupying:
                    c_type = "SWAP_CONFLICT" if is_swap else ("CELL_OVERLAP" if is_vertex else "STATIONARY_BLOCK")
                    conflict_cell = {"x": intended_pos[0], "y": intended_pos[1]}

                    # Right-of-Way Locking: Once two robots enter arbitration radius, lock the resolved priority order
                    locked_winner = self.row_tracker.get_locked_winner(
                        self.robot.robot_id, peer_snap.robot_id, m_dist
                    )
                    if locked_winner is not None:
                        i_win = (locked_winner == self.robot.robot_id)
                        winner_id = locked_winner
                        loser_id = peer_snap.robot_id if i_win else self.robot.robot_id
                        self.log(
                            f"[Tick {tick}] RIGHT-OF-WAY LOCKED: Pair ({self.robot.robot_id}, {peer_snap.robot_id}) locked -> Winner={winner_id}"
                        )
                    else:
                        my_score = round(float(self.robot.priority_score), 2)
                        peer_score = round(float(peer_snap.priority_score), 2)

                        if my_score > peer_score:
                            i_win = True
                        elif my_score < peer_score:
                            i_win = False
                        else:
                            i_win = (self.robot.robot_id < peer_snap.robot_id)

                        winner_id = self.robot.robot_id if i_win else peer_snap.robot_id
                        loser_id = peer_snap.robot_id if i_win else self.robot.robot_id

                        # Lock right of way until robots clear arbitration radius
                        self.row_tracker.lock_right_of_way(
                            self.robot.robot_id, peer_snap.robot_id, winner_id, m_dist, tick
                        )

                    self.log(
                        f"[Tick {tick}] CONFLICT DETECTED with {peer_snap.robot_id} ({c_type}) at cell ({conflict_cell['x']}, {conflict_cell['y']})! "
                        f"My Priority={self.robot.priority_score:.1f}, Peer Priority={peer_snap.priority_score:.1f}, Winner={winner_id}"
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

                        # 1. Lock all peers' current and intended cells into local_reservations for 30 ticks so A* routes around everyone
                        for dt in range(30):
                            self.local_reservations[(px, py, tick + dt)] = peer_snap.robot_id
                        for p_step in peer_snap.path:
                            px_step = int(p_step["x"])
                            py_step = int(p_step["y"])
                            for dt in range(30):
                                self.local_reservations[(px_step, py_step, tick + dt)] = peer_snap.robot_id
                        for p_other in self.peers.values():
                            if p_other.robot_id != self.robot.robot_id:
                                for dt in range(30):
                                    self.local_reservations[(p_other.position[0], p_other.position[1], tick + dt)] = p_other.robot_id
                                    self.local_reservations[(p_other.intended_pos[0], p_other.intended_pos[1], tick + dt)] = p_other.robot_id

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

                        all_blocked_cells = {
                            p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id
                        } | {
                            p.intended_pos for p in self.peers.values() if p.robot_id != self.robot.robot_id
                        }

                        detour_taken = False
                        if re_path and len(re_path) > 1:
                            next_detour = (int(re_path[1]["x"]), int(re_path[1]["y"]))
                            if next_detour != (rx, ry) and next_detour not in all_blocked_cells:
                                self.robot.path = re_path
                                intended_pos = next_detour
                                action_taken = "DETOUR_YIELD"
                                detour_taken = True
                                self.log(f"[Tick {tick}] Replanned alternate detour path ({len(re_path)} steps). Detouring to {intended_pos}.")

                        # 3. If no direct detour available, step aside into an adjacent free evasion cell (lateral nook or reverse)
                        if not detour_taken:
                            dx_rel = px - rx
                            dy_rel = py - ry
                            if dx_rel == 0 and dy_rel == 0:
                                candidate_evasions = [(rx, ry - 1), (rx, ry + 1), (rx - 1, ry), (rx + 1, ry)]
                            elif dx_rel != 0 and dy_rel != 0:
                                candidate_evasions = [(rx, ry - 1), (rx, ry + 1), (rx - 1, ry), (rx + 1, ry)]
                            elif dx_rel != 0:
                                rev_dx = -1 if dx_rel > 0 else 1
                                candidate_evasions = [(rx, ry - 1), (rx, ry + 1), (rx + rev_dx, ry), (rx - rev_dx, ry)]
                            else:
                                rev_dy = -1 if dy_rel > 0 else 1
                                candidate_evasions = [(rx - 1, ry), (rx + 1, ry), (rx, ry + rev_dy), (rx, ry - rev_dy)]

                            peer_blocked = {
                                (px, py), (pix, piy),
                                *((int(s["x"]), int(s["y"])) for s in peer_snap.path[:5])
                            }
                            other_peer_positions = {
                                p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id
                            } | {
                                p.intended_pos for p in self.peers.values() if p.robot_id != self.robot.robot_id
                            } | {
                                (int(s["x"]), int(s["y"]))
                                for p in self.peers.values()
                                if p.robot_id != self.robot.robot_id
                                for s in p.path[:3]
                            }
                            evade_cell = None
                            for cand in candidate_evasions:
                                if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                                    if self.grid.is_free(cand) and not self.grid.is_shelf_cell(cand) and cand not in peer_blocked and cand not in other_peer_positions:
                                        evade_cell = cand
                                        break

                            if evade_cell:
                                intended_pos = evade_cell
                                self.robot.path = [
                                    {"x": rx, "y": ry, "t": tick},
                                    {"x": evade_cell[0], "y": evade_cell[1], "t": tick + 1},
                                    {"x": evade_cell[0], "y": evade_cell[1], "t": tick + 2},
                                    {"x": evade_cell[0], "y": evade_cell[1], "t": tick + 3},
                                ]
                                for dt in range(4):
                                    self.local_reservations[(rx, ry, tick + dt)] = peer_snap.robot_id
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
                        # If winner's intended target cell is currently physically occupied or concurrently claimed by peer:
                        target_occupied = (intended_pos == (px, py) or intended_pos == (pix, piy))
                        if target_occupied:
                            if self.consecutive_wait_ticks < 2:
                                intended_pos = self.robot.position
                                action_taken = "WAITING"
                                self.log(f"[Tick {tick}] Pausing 1 tick at {self.robot.position} for yielding peer {peer_snap.robot_id} to clear {conflict_cell['x'], conflict_cell['y']}.")
                            else:
                                # Loser hasn't cleared after 2 ticks: winner replans around loser
                                self.log(f"[Tick {tick}] Peer {peer_snap.robot_id} unable to clear {px, py} after 2 ticks; winner seeking bypass.")
                                for dt in range(30):
                                    self.local_reservations[(px, py, tick + dt)] = peer_snap.robot_id
                                    self.local_reservations[(pix, piy, tick + dt)] = peer_snap.robot_id
                                for p_other in self.peers.values():
                                    if p_other.robot_id != self.robot.robot_id:
                                        for dt in range(30):
                                            self.local_reservations[(p_other.position[0], p_other.position[1], tick + dt)] = p_other.robot_id
                                            self.local_reservations[(p_other.intended_pos[0], p_other.intended_pos[1], tick + dt)] = p_other.robot_id

                                all_blocked_cells_w = {
                                    p.position for p in self.peers.values() if p.robot_id != self.robot.robot_id
                                } | {
                                    p.intended_pos for p in self.peers.values() if p.robot_id != self.robot.robot_id
                                }

                                bypass_found = False
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
                                        is_turn_w = (next_w == self.robot.position and len(w_bypass) > 2)
                                        if (next_w != self.robot.position or is_turn_w) and next_w not in all_blocked_cells_w:
                                            self.robot.path = w_bypass
                                            intended_pos = next_w
                                            action_taken = "DETOUR_WINNER"
                                            bypass_found = True
                                            self.log(f"[Tick {tick}] Winner bypassing stationary peer to {next_w}.")
                                if not bypass_found:
                                    intended_pos = self.robot.position
                                    action_taken = "WAITING"
                                    self.log(f"[Tick {tick}] Bypass around stationary peer unavailable; holding position at {self.robot.position}.")

                        conflict_resolved = {
                            "winner_id": winner_id,
                            "loser_id": loser_id,
                            "action": action_taken if "DETOUR_WINNER" in action_taken else "PROCEED",
                            "type": c_type,
                            "cell": conflict_cell,
                        }
                        break

        # 10. Check Degraded Mode speed throttle
        # If proximity filtering is active, only peers expected to be in proximity can trigger degraded mode
        is_net_degraded = False
        if self.degraded_detector.forced_degraded:
            is_net_degraded = True
        elif self.degraded_detector.peer_last_ticks:
            for peer_id, last_t in self.degraded_detector.peer_last_ticks.items():
                if self.is_peer_in_proximity(peer_id):
                    if (tick - last_t) >= self.degraded_detector.threshold_missing_ticks:
                        is_net_degraded = True
                        break

        if intended_pos != prev_pos and is_net_degraded and (tick % 2 != 0):
            intended_pos = prev_pos
            action_taken = "DEGRADED_SPEED_PAUSE"
            self.log(f"[Tick {tick}] Degraded network throttle: pausing movement on alternate tick.")

        # 11. Check Task Realism Load Pause (Part D: proportional to weight, applies to pod and carton carry legs)
        if intended_pos != prev_pos and (self.fsm.state == RobotState.EN_ROUTE_DROPOFF or self.robot.carrying_pod_id):
            p_weight = getattr(self.task, "payload_weight_kg", 0.0) if self.task else 0.0
            if p_weight == 0.0 and self.robot.carrying_pod_id and getattr(self, "inventory_ledger", None):
                p_weight = self.inventory_ledger.get_shelf_weight_kg(self.robot.carrying_pod_id)

            if p_weight > 0.0 and self.load_move_steps > 0:
                # Heavy pod (>= 50kg): pause every 2 steps
                # Medium load (20 - 50kg): pause every 3 steps
                # Light load (< 20kg): pause every 4 steps
                if p_weight >= 50.0:
                    pause_interval = 2
                elif p_weight >= 20.0:
                    pause_interval = 3
                else:
                    pause_interval = 4

                if self.load_move_steps % pause_interval == 0:
                    intended_pos = prev_pos
                    action_taken = "LOAD_WEIGHT_PAUSE"
                    self.load_move_steps += 1
                    self.log(f"[Tick {tick}] Load weight inertia pause (carrying {p_weight}kg, interval={pause_interval}).")

        # 11.4. Physical & Concurrent occupancy guard: never step into a cell occupied or claimed by a peer
        if intended_pos != prev_pos:
            for p in self.peers.values():
                if p.robot_id != self.robot.robot_id:
                    if p.position == intended_pos:
                        self.log(f"[Tick {tick}] SAFETY HALT: Peer {p.robot_id} physically occupies intended cell {intended_pos}. Waiting.")
                        intended_pos = prev_pos
                        action_taken = "WAITING"
                        break
                    elif p.intended_pos == intended_pos and p.last_seen_tick >= tick - 2:
                        my_score = round(float(self.robot.priority_score), 2)
                        peer_score = round(float(p.priority_score), 2)
                        i_win = (my_score > peer_score) or (my_score == peer_score and self.robot.robot_id < p.robot_id)
                        if not i_win:
                            self.log(f"[Tick {tick}] SAFETY HALT: Peer {p.robot_id} concurrently claims intended cell {intended_pos} with higher priority. Yielding.")
                            intended_pos = prev_pos
                            action_taken = "WAITING"
                            break

        # 11.5. Broadcast Updated Intention if arbitration, safety halt, or throttling altered intended_pos
        if list(intended_pos) != claim_payload.get("intended_pos"):
            self.seq += 1
            claim_payload["intended_pos"] = [intended_pos[0], intended_pos[1]]
            claim_payload["action"] = action_taken
            update_envelope = sign_payload(claim_payload, secret_key=self.secret_key, seq=self.seq)
            for peer_id in self.peer_ports.keys():
                if peer_id != self.robot.robot_id:
                    self.transport.send(peer_id, update_envelope)

        # 12. Commit Movement / Turn / Wait
        if intended_pos == prev_pos:
            if self.fsm.state == RobotState.IDLE or not self.robot.path:
                if action_taken not in ("MISSION_COMPLETED",):
                    action_taken = "IDLE"
            elif len(self.robot.path) > 1 and (int(self.robot.path[1]["x"]), int(self.robot.path[1]["y"])) == prev_pos:
                # Check if this stationary step is an actual heading turn to face next waypoint
                did_turn = False
                if len(self.robot.path) > 2:
                    dx_next = int(self.robot.path[2]["x"]) - prev_pos[0]
                    dy_next = int(self.robot.path[2]["y"]) - prev_pos[1]
                    target_h = None
                    if dx_next > 0: target_h = Heading.EAST
                    elif dx_next < 0: target_h = Heading.WEST
                    elif dy_next > 0: target_h = Heading.SOUTH
                    elif dy_next < 0: target_h = Heading.NORTH
                    if target_h and target_h != self.robot.heading:
                        self.robot.heading = target_h
                        did_turn = True
                self.robot.path = self.robot.path[1:]
                if did_turn:
                    action_taken = "TURNED"
                    self.consecutive_wait_ticks = 0
                else:
                    self.consecutive_wait_ticks += 1
                    if action_taken not in ("YIELDED / BRAKED", "LOAD_WEIGHT_PAUSE", "DEGRADED_SPEED_PAUSE"):
                        self.robot.wait_ticks_so_far += 1
                        action_taken = "WAITING"
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
                elif self.pre_conflict_activity == RobotState.IDLE:
                    self.fsm.transition(RobotEvent.RESUME_IDLE)
                else:
                    self.fsm.state = RobotState.FAILSAFE_HOLD
                self.pre_conflict_activity = None
                self.robot.state = self.fsm.state
                self.log(f"[Tick {tick}] Conflict cleared: resumed state {self.fsm.state.value}.")

        self.robot.last_updated_tick = tick

        # Check mission waypoint arrival
        if self.task:
            if self.fsm.state == RobotState.EN_ROUTE_PICKUP and self.robot.position == self.task.pickup:
                # Phase 1.5 Fix 1: Atomically verify pod claim before transitioning into PICKING/LIFTING
                if self.robot_type == "GOODS_TO_PERSON":
                    shelf_id = getattr(self.task, "target_shelf_id", None) or self.world.shelf_at(self.robot.position[0], self.robot.position[1])
                    if not shelf_id:
                        nearby = [sid for sid, pos in self.world.pod_slots.items() if abs(pos[0] - self.robot.position[0]) + abs(pos[1] - self.robot.position[1]) <= 1]
                        if nearby:
                            shelf_id = nearby[0]
                    if shelf_id:
                        if not self.claim_pod_resource(shelf_id, tick):
                            self.log(f"[Tick {tick}] POD CLAIM REJECTED at pickup: {shelf_id} already claimed by another robot. Transitioning to FAILSAFE_HOLD.")
                            self.fsm.state = RobotState.FAILSAFE_HOLD
                            self.robot.state = self.fsm.state
                            return self._build_telemetry_frame(tick, "POD_CLAIM_CONFLICT", None)
                        self.set_pod_slot_occupant(self.robot.position, self.robot.robot_id, shelf_id=shelf_id, tick=tick)

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
                scan_res = self.active_audit_mission.record_scan(
                    cell=self.robot.position,
                    robot_id=self.robot.robot_id,
                    tick=tick,
                    ledger=self.inventory_ledger,
                    world=self.world,
                )
                self.fsm.transition(RobotEvent.AUDIT_CHECKPOINT_LOGGED)
                self.robot.state = self.fsm.state
                
                # Broadcast inventory update to all peers + HaLow dashboard channel
                if scan_res.get("shelf_id"):
                    self.broadcast_inventory_update(
                        shelf_id=scan_res["shelf_id"],
                        current_tick=tick,
                        sku_manifest=scan_res.get("sku_manifest"),
                        box_count=scan_res.get("box_count"),
                        confidence=scan_res.get("confidence", 1.0),
                    )

                if self.active_audit_mission.audit_id:
                    self.completed_task_ids.add(self.active_audit_mission.audit_id)
                self.active_audit_mission = None
                self.goal_pos = None
                self.robot.path = []
                action_taken = "COMPLETED"
                self.log(f"[Tick {tick}] {scan_res['message']}")
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

        # ── Reactive Physical Proximity Override (Part 1D) ──────────────
        # Low-level safety net: drain latest peer broadcasts right now to catch
        # any concurrent movements committed during this tick.
        self._drain_inbox(tick)
        my_pos = self.robot.position
        for peer in self.peers.values():
            if peer.robot_id == self.robot.robot_id:
                continue
            p_pos = peer.position
            p_intent = peer.intended_pos

            # Check for physical vertex overlap or concurrent entry or edge swap
            collision = False
            if p_pos is not None and my_pos == p_pos:
                collision = True
            elif p_intent is not None and my_pos == p_intent and my_pos != prev_pos:
                collision = True
            elif p_pos is not None and p_intent is not None and my_pos == p_pos and prev_pos == p_intent:
                collision = True

            if collision:
                if my_pos != prev_pos:
                    self.robot.position = prev_pos
                    self.robot.heading = prev_heading
                    action_taken = "PROXIMITY_BRAKE"
                    self.robot.wait_ticks_so_far += 1
                    self._needs_replan = True
                    self.log(
                        f"[Tick {tick}] PROXIMITY OVERRIDE: Collision danger prevented with "
                        f"{peer.robot_id} (my_pos={my_pos}, peer_pos={p_pos}, peer_intent={p_intent}). "
                        f"Reverting to {prev_pos}, triggering re-arbitration."
                    )
                else:
                    # Both ended up in the same cell while one was stationary;
                    # immediately step aside into an adjacent free neighbor to avoid multi-tick overlap
                    free_nbs = [nb for nb in self.grid._free_neighbors(my_pos) if nb not in {p.position for p in self.peers.values()}]
                    if free_nbs:
                        self.robot.position = free_nbs[0]
                        action_taken = "EMERGENCY_EVADE"
                        self._needs_replan = True
                        self.log(
                            f"[Tick {tick}] PROXIMITY OVERRIDE: Evading stationary collision with "
                            f"{peer.robot_id} at {my_pos} -> stepped aside to {free_nbs[0]}."
                        )
                break
        # ────────────────────────────────────────────────────────────────

        # ── G2P Shelf Violation Guard (Part 2D) ─────────────────────────
        # If a laden robot (carrying pod) has stepped onto any shelf cell, ESTOP.
        if hasattr(self, '_carrying_pod') and self._carrying_pod:
            if hasattr(self.grid, 'is_shelf_cell') and self.grid.is_shelf_cell(self.robot.position):
                # Check if this is the robot's own docking shelf — that's allowed
                own_shelf = getattr(self, '_docking_shelf_pos', None)
                if own_shelf is None or self.robot.position != own_shelf:
                    self.fsm.state = RobotState.EMERGENCY_STOP
                    self.robot.state = self.fsm.state
                    self.robot.path = []
                    action_taken = "ESTOP_SHELF_VIOLATION"
                    self.log(
                        f"[Tick {tick}] ESTOP: Laden robot stepped onto shelf cell {self.robot.position}! "
                        f"Aborting execution."
                    )
        # ────────────────────────────────────────────────────────────────

        self.log(
            f"[Tick {tick}] Pos={self.robot.position}, Heading={self.robot.heading.value}, "
            f"State={self.fsm.state.value}, Action={action_taken}, Priority={self.robot.priority_score:.1f}, "
            f"Battery={self.robot.battery_pct:.1f}%, Waits={self.robot.wait_ticks_so_far}"
        )

        return self._build_telemetry_frame(tick, action_taken, conflict_resolved)

    def _nearest_available_charger(
        self,
        current_tick: Optional[int] = None,
        filter_reachable: bool = True,
        claim: bool = False,
    ) -> Optional[Tuple[int, int]]:
        from app.services.reservations import get_charger_claim, claim_charger
        tick = current_tick if current_tick is not None else getattr(self.robot, "last_updated_tick", 0)

        occupied = {
            peer.position for peer in self.peers.values()
            if peer.state == RobotState.CHARGING and peer.robot_id != self.robot.robot_id
        } | {
            peer.intended_pos for peer in self.peers.values()
            if peer.intended_pos in self.charging_stations and peer.robot_id != self.robot.robot_id
        } | {
            (p["x"], p["y"])
            for peer in self.peers.values()
            if peer.robot_id != self.robot.robot_id
            for p in (peer.path or [])
            if (p["x"], p["y"]) in self.charging_stations
        } | {
            peer.charger_target
            for peer in self.peers.values()
            if getattr(peer, "charger_target", None) is not None and peer.robot_id != self.robot.robot_id
        }

        candidates = []
        for station in self.charging_stations:
            dist = abs(station[0] - self.robot.position[0]) + abs(station[1] - self.robot.position[1])
            if filter_reachable and (dist * self.energy_per_cell > self.robot.battery_pct):
                continue
            if station in occupied:
                continue
            holder = get_charger_claim(station, tick)
            if holder is not None and holder != self.robot.robot_id:
                continue
            candidates.append((dist, station))

        if not candidates:
            return None

        candidates.sort(key=lambda s: (s[0], s[1][0], s[1][1]))

        if claim:
            for dist, station in candidates:
                if self.claim_charger_resource(station, tick):
                    return station
            return None

        return candidates[0][1]

    def _build_telemetry_frame(self, tick: int, action: str, conflict: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        frame = {
            "tick": tick,
            "robot_id": self.robot.robot_id,
            "robot_type": self.robot_type,
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
            "path": [{"x": p["x"], "y": p["y"], "t": tick + i} for i, p in enumerate(self.robot.path[:8])],
            "goal": list(self.goal_pos) if self.goal_pos else list(self.robot.position),
            "conflict": conflict,
            "local_inventory_count": len(self.local_inventory_cache),
            "carrying_pod_id": self.robot.carrying_pod_id,
            "halow_status": self.halow_transport.get_status() if hasattr(self, "halow_transport") else None,
            "is_interim_coordinator": self.is_interim_coordinator,
            "interim_coordinator_id": self.interim_coordinator_id,
            "proximity_radius": self.proximity_radius,
            "packets_sent_count": self.packets_sent_count,
            "packets_filtered_count": self.packets_filtered_count,
        }
        if self.telemetry_queue is not None:
            try:
                self.telemetry_queue.put_nowait(frame)
            except Exception:
                pass
        return frame

    def _validate_station_command_authorization(
        self,
        actual_msg: Dict[str, Any],
        sender: str,
        is_signed: bool,
        envelope: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Receiving-end zero-trust authorization boundary for station commands.
        Validates Ed25519 cryptographic envelope, station_role claim, and task scope allowlist.
        ImportStation commands are rejected if outside import scope; ExportStation commands
        are rejected if outside export/sortation scope; AuthorityStation is unrestricted.
        """
        station_role = actual_msg.get("station_role")

        # If not claiming to be a station and sender is not a station node, pass through to normal peer handling
        if not station_role and sender not in ("IMPORT_STATION", "EXPORT_STATION", "AUTHORITY_STATION"):
            return True, ""

        # Zero-Trust Check 1: Must be cryptographically signed
        if not is_signed:
            reason = (
                f"STATION_AUTHORITY_VIOLATION: Robot {self.robot.robot_id} rejected station command "
                f"from '{sender}' because message lacks a valid HMAC-SHA256 signature envelope."
            )
            return False, reason

        # Zero-Trust Check 1b: Station directives must be asymmetrically signed with Ed25519 (SEC-01)
        algo = envelope.get("algorithm") if isinstance(envelope, dict) else None
        if envelope is not None and algo != "ed25519":
            reason = (
                f"STATION_AUTHORITY_VIOLATION: Robot {self.robot.robot_id} rejected station command "
                f"from '{sender}' because message was signed with symmetric HMAC '{algo}' rather than "
                f"Station Ed25519 private key. Shared symmetric HMAC signatures are forbidden for stations."
            )
            return False, reason

        # Zero-Trust Check 2: station_role must be provided
        if not station_role:
            reason = (
                f"STATION_AUTHORITY_VIOLATION: Robot {self.robot.robot_id} rejected command from "
                f"station '{sender}' because message lacks required 'station_role' claim."
            )
            return False, reason

        t_dict = actual_msg.get("task", {})
        task_type = str(t_dict.get("task_type", "")).upper()
        tid = t_dict.get("task_id", "UNKNOWN")
        source_gate = str(t_dict.get("source_gate", "")).upper()
        destination_gate = str(t_dict.get("destination_gate", "")).upper()
        pickup = t_dict.get("pickup")
        dropoff = t_dict.get("dropoff")

        # Zero-Trust Check 3: Role Allowlist
        if station_role == "IMPORT_STATION":
            is_allowed = False
            if task_type in ("INDUCT_BATCH", "IMPORT_UNLOAD"):
                is_allowed = True
            elif source_gate in ("IN-1", "IN-2", "IN-3"):
                is_allowed = True
            elif pickup and isinstance(pickup, (list, tuple)) and len(pickup) >= 2:
                if int(pickup[0]) <= 2 and 8 <= int(pickup[1]) <= 20:
                    is_allowed = True

            if not is_allowed:
                reason = (
                    f"STATION_AUTHORITY_VIOLATION: Robot {self.robot.robot_id} rejected command from "
                    f"IMPORT_STATION for out-of-scope task '{tid}' (task_type='{task_type}', "
                    f"source_gate='{source_gate}', destination_gate='{destination_gate}'). "
                    f"ImportStation has no authority over export/general tasks."
                )
                return False, reason

        elif station_role == "EXPORT_STATION":
            is_allowed = False
            if task_type in ("CONSOLIDATE_EXPORT", "TRANSFER_TO_SORTATION", "DECANT_TO_CHUTE"):
                is_allowed = True
            elif destination_gate in ("OUT-1", "OUT-2", "OUT-3"):
                is_allowed = True
            elif dropoff and isinstance(dropoff, (list, tuple)) and len(dropoff) >= 2:
                dx, dy = int(dropoff[0]), int(dropoff[1])
                if (dx >= 27 and 8 <= dy <= 20) or (21 <= dx <= 27 and 2 <= dy <= 5):
                    is_allowed = True

            if not is_allowed:
                reason = (
                    f"STATION_AUTHORITY_VIOLATION: Robot {self.robot.robot_id} rejected command from "
                    f"EXPORT_STATION for out-of-scope task '{tid}' (task_type='{task_type}', "
                    f"source_gate='{source_gate}', destination_gate='{destination_gate}'). "
                    f"ExportStation has no authority over import/general tasks."
                )
                return False, reason

        elif station_role == "AUTHORITY_STATION":
            return True, ""

        else:
            reason = (
                f"STATION_AUTHORITY_VIOLATION: Robot {self.robot.robot_id} rejected command with "
                f"unrecognized station_role '{station_role}' from sender '{sender}'."
            )
            return False, reason

        return True, ""

    def is_peer_in_proximity(self, peer_id: str) -> bool:
        """
        NET-01 Proximity Filter: Checks whether peer_id is within self.proximity_radius.
        Returns True if:
        1. proximity_radius is disabled (None or <= 0).
        2. peer position is unknown (allows initial discovery).
        3. peer is a fixed infrastructure station (ensures station observability).
        4. Euclidean distance to peer <= self.proximity_radius.
        """
        if self.proximity_radius is None or self.proximity_radius <= 0:
            return True

        # Non-AMR nodes (fixed stations) are never filtered out by proximity
        if not peer_id.startswith("AMR-") and peer_id in ("IMPORT_STATION", "EXPORT_STATION", "AUTHORITY_STATION"):
            return True

        peer_snap = self.peers.get(peer_id)
        if peer_snap is None or peer_snap.position is None:
            # Unknown peer position: allow broadcast for initial discovery
            return True

        px, py = peer_snap.position
        rx, ry = self.robot.position
        dist = ((rx - px) ** 2 + (ry - py) ** 2) ** 0.5
        return dist <= self.proximity_radius

    def _drain_inbox(self, current_tick: int) -> None:
        """Drains incoming transport messages, verifies security envelopes, updates peer snapshots."""
        raw_messages = self.transport.recv_all()
        for msg in raw_messages:
            is_signed = False
            # Verify security envelope if present
            if "signature" in msg and "body" in msg:
                valid, payload, err = verify_envelope(msg, secret_key=self.secret_key)
                if not valid or not payload:
                    self.log(f"Security envelope verification failed: {err}")
                    continue
                body = msg.get("body", {})
                sender = (
                    payload.get("sender_id")
                    or payload.get("robot_id")
                    or payload.get("station_id")
                    or payload.get("bidder_id")
                    or payload.get("winner_id")
                    or body.get("sender_id", "unknown")
                )
                seq = body.get("seq")
                ts = body.get("timestamp", time.time())
                r_valid, r_err = self.replay_guard.validate(sender, seq, ts)
                if not r_valid:
                    self.log(f"Security replay guard rejected packet from {sender}: {r_err}")
                    continue
                actual_msg = payload
                is_signed = True
            else:
                actual_msg = msg
                sender = (
                    actual_msg.get("sender_id")
                    or actual_msg.get("robot_id")
                    or actual_msg.get("station_id", "unknown")
                )
                is_signed = False

            m_type = actual_msg.get("type")

            # Zero-Trust Station Command Authorization Enforcement on Receiving End
            if m_type in ("TASK_ASSIGNMENT", "TASK_ANNOUNCEMENT", "TASK_REROUTE", "STATION_COMMAND"):
                auth_ok, auth_err = self._validate_station_command_authorization(
                    actual_msg, sender, is_signed, envelope=msg if "signature" in msg else None
                )
                if not auth_ok:
                    self.rejected_station_commands.append({
                        "tick": current_tick,
                        "sender": sender,
                        "message_type": m_type,
                        "reason": auth_err,
                        "task": actual_msg.get("task"),
                    })
                    self.log(f"[Tick {current_tick}] {auth_err}")
                    continue

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
                old_snap = self.peers.get(sender_id)
                if old_snap is not None and msg_tick < old_snap.last_seen_tick:
                    # Discard stale / out-of-order UDP packet from earlier tick
                    continue
                self.degraded_detector.record_peer_tick(sender_id, msg_tick)
                c_target = actual_msg.get("charger_target")
                charger_target_tuple = (int(c_target[0]), int(c_target[1])) if c_target else None
                if old_snap and old_snap.charger_target and old_snap.charger_target != charger_target_tuple:
                    from app.services.reservations import release_charger
                    release_charger(old_snap.charger_target, sender_id)
                if charger_target_tuple:
                    if self.charger_target == charger_target_tuple:
                        self._resolve_resource_contention("CHARGER", [charger_target_tuple[0], charger_target_tuple[1]], sender_id, float(actual_msg.get("priority_score", 0.0)), msg_tick)
                    else:
                        from app.services.reservations import record_peer_charger_claim
                        record_peer_charger_claim(charger_target_tuple, sender_id, msg_tick)

                peer_claimed_pods = set()
                claimed_pod = actual_msg.get("claimed_pod_id")
                if claimed_pod:
                    peer_claimed_pods.add(str(claimed_pod))
                for pid in actual_msg.get("active_claimed_pods", []):
                    peer_claimed_pods.add(str(pid))

                for pid in peer_claimed_pods:
                    if pid in self.active_claimed_pods:
                        self._resolve_resource_contention("POD", pid, sender_id, float(actual_msg.get("priority_score", 0.0)), msg_tick)
                    else:
                        from app.services.reservations import record_peer_pod_claim
                        record_peer_pod_claim(pid, sender_id, msg_tick)

                peer_rt = actual_msg.get("robot_type")
                if peer_rt:
                    self.fleet_roster[sender_id] = peer_rt

                peer_occ = actual_msg.get("occupied_slot")
                peer_occ_tuple = (int(peer_occ[0]), int(peer_occ[1])) if peer_occ else None

                if old_snap and old_snap.occupied_slot and old_snap.occupied_slot != peer_occ_tuple:
                    if hasattr(self.grid, "set_pod_slot_occupant"):
                        cur_occ = getattr(self.grid, "pod_slot_occupants", {}).get(old_snap.occupied_slot)
                        if cur_occ == sender_id:
                            self.grid.set_pod_slot_occupant(old_snap.occupied_slot, None)
                if peer_occ_tuple:
                    if hasattr(self.grid, "set_pod_slot_occupant"):
                        self.grid.set_pod_slot_occupant(peer_occ_tuple, sender_id)

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
                    robot_type=peer_rt,
                    occupied_slot=peer_occ_tuple,
                )
                self.peers[sender_id] = snap

                # Update local reservation table
                for k in [k for k, v in list(self.local_reservations.items()) if v == sender_id]:
                    del self.local_reservations[k]
                if snap.path:
                    max_dt = 0
                    for p in snap.path:
                        p_step_t = int(p.get("t", msg_tick))
                        dt = max(0, p_step_t - msg_tick)
                        max_dt = max(max_dt, dt)
                        rec_t = current_tick + dt
                        self.local_reservations[(int(p["x"]), int(p["y"]), rec_t)] = sender_id
                    # Extend final step across planning horizon so peers don't path through the stopped robot
                    last_p = snap.path[-1]
                    lx, ly = int(last_p["x"]), int(last_p["y"])
                    for dt in range(max_dt + 1, self.HOLD):
                        self.local_reservations[(lx, ly, current_tick + dt)] = sender_id
                else:
                    # Stationary/parked at snap.intended_pos or snap.position
                    sx, sy = snap.intended_pos if snap.intended_pos else snap.position
                    for dt in range(self.HOLD):
                        self.local_reservations[(sx, sy, current_tick + dt)] = sender_id

            elif m_type == "TASK_ASSIGNMENT":
                # Handle task assignment message from central dispatcher (legacy / direct mode)
                t_dict = actual_msg.get("task", {})
                tid = t_dict.get("task_id")
                if tid and tid not in self.completed_task_ids and self.fsm.state == RobotState.IDLE:
                    pickup_pos = tuple(t_dict["pickup"]) if "pickup" in t_dict else tuple(self.robot.position)
                    dropoff_pos = tuple(t_dict["dropoff"])
                    if tid.startswith("AUDIT") or self.robot_type == "SCANNING_AUDIT":
                        from app.services.audit_mission import AuditMission
                        self.active_audit_mission = AuditMission(dropoff_pos, audit_id=tid)
                        self.goal_pos = dropoff_pos
                        self.fsm.transition(RobotEvent.START_AUDIT)
                        self.robot.state = self.fsm.state
                        audit_path = self._timed_find_path(
                            start=self.robot.position,
                            goal=dropoff_pos,
                            current_tick=current_tick,
                            reservation_table=self.local_reservations,
                            grid=self.grid,
                        )
                        if audit_path and len(audit_path) > 1:
                            self.robot.path = audit_path
                            reserve_path(audit_path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
                        else:
                            self.robot.path = [{"x": self.robot.position[0], "y": self.robot.position[1], "t": current_tick}]
                        self.log(f"[Tick {current_tick}] Accepted AUDIT mission {tid} to checkpoint {dropoff_pos}.")
                    else:
                        self._assign_initial_task(
                            goal_pos=dropoff_pos,
                            urgency=int(t_dict.get("urgency", 3)),
                            payload_weight_kg=float(t_dict.get("payload_weight_kg", 0.0)),
                            task_id=tid,
                            pickup_pos=pickup_pos,
                            task_type=t_dict.get("task_type", "STANDARD"),
                            target_shelf_id=t_dict.get("target_shelf_id"),
                            sku_to_pick=t_dict.get("sku_to_pick"),
                            quantity=int(t_dict.get("quantity", 1)),
                            destination_zone=t_dict.get("destination_zone"),
                            pick_station_id=t_dict.get("pick_station_id"),
                        )
                        self.log(f"[Tick {current_tick}] Accepted TASK_ASSIGNMENT {tid} to pickup {pickup_pos} -> dropoff {dropoff_pos}.")

            elif m_type == "TASK_ANNOUNCEMENT":
                # Handle decentralized contract-net announcement broadcast
                t_dict = actual_msg.get("task", {})
                tid = t_dict.get("task_id")
                task_type_str = t_dict.get("task_type", "STANDARD")

                # Explicit decoupled robot eligibility matching
                if task_type_str in ("INDUCT_BATCH", "DECANT_TO_CHUTE", "CONSOLIDATE_EXPORT", "TRANSFER_TO_SORTATION"):
                    is_eligible = (self.robot_type == "SORTING")
                elif task_type_str in ("RETRIEVE_POD", "RETURN_POD", "PICK_ITEM"):
                    is_eligible = (self.robot_type == "GOODS_TO_PERSON")
                    target_shelf = t_dict.get("target_shelf_id")
                    if target_shelf:
                        from app.services.reservations import get_pod_claim
                        if get_pod_claim(target_shelf, current_tick) is not None:
                            is_eligible = False
                elif task_type_str == "AUDIT":
                    is_eligible = (self.robot_type == "SCANNING_AUDIT")
                else:
                    is_eligible = (self.robot_type in ("GOODS_TO_PERSON", "SORTING"))

                # Check battery feasibility before bidding (path length + margin)
                pickup_pos = tuple(t_dict["pickup"]) if "pickup" in t_dict else tuple(self.robot.position)
                dropoff_pos = tuple(t_dict["dropoff"]) if "dropoff" in t_dict else pickup_pos
                dist_to_pickup = abs(self.robot.position[0] - pickup_pos[0]) + abs(self.robot.position[1] - pickup_pos[1])
                dist_pickup_to_drop = abs(dropoff_pos[0] - pickup_pos[0]) + abs(dropoff_pos[1] - pickup_pos[1])
                trip_dist = dist_to_pickup + dist_pickup_to_drop
                if t_dict.get("return_to_home"):
                    trip_dist += dist_pickup_to_drop
                needed_battery = trip_dist * self.energy_per_cell * self.charging_safety_margin + self.charging_reserve_pct
                if self.robot.battery_pct < needed_battery:
                    is_eligible = False
                    self.log(
                        f"[Tick {current_tick}] Contract-Net: Ineligible for {tid} due to low battery "
                        f"({self.robot.battery_pct:.1f}% < {needed_battery:.1f}% needed for trip)."
                    )

                if (
                    tid
                    and tid not in self.completed_task_ids
                    and tid not in self.known_task_claims
                    and (tid not in self.active_bids or self.active_bids[tid].get("claimed", False))
                    and self.fsm.state == RobotState.IDLE
                    and not self.task
                    and is_eligible
                ):
                    battery_penalty = max(0.0, (100.0 - self.robot.battery_pct) * 0.5)
                    queued_work_penalty = 50.0 if (self.task or self.fsm.state != RobotState.IDLE) else 0.0
                    urgency_weight = (int(t_dict.get("urgency", 3)) - 1) * 2.0
                    bid_score = max(0.1, float(dist_to_pickup * 10.0 + battery_penalty + queued_work_penalty - urgency_weight))

                    self.active_bids[tid] = {
                        "announced_tick": current_tick,
                        "task_dict": t_dict,
                        "my_bid": bid_score,
                        "peer_bids": {},
                        "claimed": False,
                    }

                    # Broadcast signed TASK_BID to peers
                    self.seq += 1
                    bid_payload = {
                        "type": "TASK_BID",
                        "sender_id": self.robot.robot_id,
                        "robot_id": self.robot.robot_id,
                        "task_id": tid,
                        "bidder_id": self.robot.robot_id,
                        "bid_score": round(bid_score, 2),
                        "tick": current_tick,
                    }
                    envelope = sign_payload(bid_payload, secret_key=self.secret_key, seq=self.seq)
                    for peer_id in self.peer_ports.keys():
                        if peer_id != self.robot.robot_id:
                            self.transport.send(peer_id, envelope)
                    self.log(f"[Tick {current_tick}] Contract-Net: Received TASK_ANNOUNCEMENT {tid}. Broadcasted TASK_BID={bid_score:.1f}.")

            elif m_type == "TASK_BID":
                # Handle competing bid from a peer AMR
                tid = actual_msg.get("task_id")
                bidder = actual_msg.get("bidder_id")
                score = float(actual_msg.get("bid_score", float("inf")))
                if tid in self.active_bids:
                    self.active_bids[tid]["peer_bids"][bidder] = score
                    self.log(f"[Tick {current_tick}] Contract-Net: Received TASK_BID from {bidder} for {tid}: {score:.1f}")

            elif m_type == "TASK_CLAIM":
                # Handle task claim announcement from the winning AMR
                tid = actual_msg.get("task_id")
                winner = actual_msg.get("winner_id")
                peer_score = float(actual_msg.get("bid_score", 0.0))
                lease_ticks = int(actual_msg.get("lease_ticks", 40))
                self.known_task_claims.add(tid)
                self.task_leases[tid] = {
                    "holder": winner,
                    "expires_tick": current_tick + lease_ticks,
                    "bid_score": peer_score,
                }
                if tid in self.active_bids:
                    self.active_bids[tid]["claimed"] = True
                    if winner != self.robot.robot_id:
                        self.log(f"[Tick {current_tick}] Contract-Net: Peer {winner} claimed task {tid}. Standing down.")

                # Deterministic duplicate claim resolution
                if self.task and self.task.task_id == tid and winner != self.robot.robot_id:
                    my_score = self.active_bids.get(tid, {}).get("my_bid", 999.0)
                    if (peer_score, winner) < (my_score, self.robot.robot_id):
                        self.log(
                            f"[Tick {current_tick}] DUPLICATE CLAIM CONFLICT: Standing down from {tid} "
                            f"in favor of peer {winner} ({peer_score:.1f} < my {my_score:.1f})."
                        )
                        if getattr(self.task, "target_shelf_id", None):
                            self.release_pod_resource(self.task.target_shelf_id, current_tick)
                        self.task = None
                        self.robot.current_task_id = None
                        self.robot.path = []
                        self.fsm.state = RobotState.IDLE
                        self.robot.state = RobotState.IDLE

            elif m_type == "TASK_LEASE_HEARTBEAT":
                tid = actual_msg.get("task_id")
                holder = actual_msg.get("robot_id") or actual_msg.get("sender_id")
                lease_ticks = int(actual_msg.get("lease_ticks", 40))
                if tid:
                    self.task_leases[tid] = {
                        "holder": holder,
                        "expires_tick": current_tick + lease_ticks,
                    }

            elif m_type == "TASK_COMPLETED":
                tid = actual_msg.get("task_id")
                if tid:
                    self.completed_task_ids.add(tid)
                    self.task_leases.pop(tid, None)
                    self.known_task_claims.add(tid)


            elif m_type == "INVENTORY_UPDATE":
                shelf_id = actual_msg.get("shelf_id")
                if shelf_id:
                    manifest = actual_msg.get("sku_manifest", {})
                    box_count = int(actual_msg.get("current_box_count", sum(manifest.values())))
                    conf = float(actual_msg.get("confidence", 1.0))
                    tick_val = int(actual_msg.get("tick", current_tick))
                    src_bot = str(actual_msg.get("source_robot_id") or actual_msg.get("sender_id") or "UNKNOWN")
                    sx = int(actual_msg.get("x", 0))
                    sy = int(actual_msg.get("y", 0))
                    incoming_ver = int(actual_msg.get("version", 1))

                    existing = self.local_inventory_cache.get(shelf_id)
                    should_apply = True
                    if existing is not None:
                        curr_ver = getattr(existing, "version", 1)
                        curr_conf = existing.confidence
                        curr_tick = existing.last_audited_tick

                        if incoming_ver > curr_ver:
                            should_apply = True
                        elif incoming_ver == curr_ver:
                            if conf > curr_conf:
                                should_apply = True
                            elif conf == curr_conf and tick_val > curr_tick:
                                should_apply = True
                            else:
                                should_apply = False
                        else:
                            should_apply = False

                    if should_apply:
                        rec = ShelfRecord(
                            shelf_id=shelf_id,
                            x=sx,
                            y=sy,
                            capacity_boxes=80,
                            current_box_count=box_count,
                            sku_manifest=dict(manifest),
                            last_audited_tick=tick_val,
                            last_audited_by=src_bot,
                            confidence=conf,
                            version=incoming_ver,
                        )
                        self.local_inventory_cache[shelf_id] = rec
                        if getattr(self, "inventory_ledger", None):
                            self.inventory_ledger.upsert_shelf(rec)
                        self.log(f"[Tick {current_tick}] Decentralized INVENTORY_UPDATE applied for {shelf_id} (v={incoming_ver}, count={box_count}, conf={conf:.2f}) from peer {src_bot}.")
                    else:
                        self.log(f"[Tick {current_tick}] Decentralized INVENTORY_UPDATE discarded for {shelf_id} (v={incoming_ver} <= local v={getattr(existing, 'version', 1)}).")

            elif m_type == "RESOURCE_CLAIM":
                res_type = actual_msg.get("resource_type")
                res_id = actual_msg.get("resource_id")
                claiming_bot = str(actual_msg.get("robot_id") or actual_msg.get("sender_id") or "UNKNOWN")
                c_tick = int(actual_msg.get("tick", current_tick))
                l_ticks = int(actual_msg.get("lease_ticks", 40))
                peer_priority = float(actual_msg.get("priority_score", 0.0))

                if claiming_bot != self.robot.robot_id:
                    if res_type == "POD" and res_id:
                        shelf_str = str(res_id)
                        if shelf_str in self.active_claimed_pods:
                            self._resolve_resource_contention("POD", shelf_str, claiming_bot, peer_priority, c_tick, l_ticks)
                        else:
                            from app.services.reservations import record_peer_pod_claim
                            record_peer_pod_claim(shelf_str, claiming_bot, c_tick, l_ticks)

                    elif res_type == "CHARGER" and res_id:
                        station_tuple = (int(res_id[0]), int(res_id[1]))
                        if self.charger_target == station_tuple:
                            self._resolve_resource_contention("CHARGER", [station_tuple[0], station_tuple[1]], claiming_bot, peer_priority, c_tick, l_ticks)
                        else:
                            from app.services.reservations import record_peer_charger_claim
                            record_peer_charger_claim(station_tuple, claiming_bot, c_tick, l_ticks)

            elif m_type == "RESOURCE_RELEASE":
                res_type = actual_msg.get("resource_type")
                res_id = actual_msg.get("resource_id")
                releasing_bot = str(actual_msg.get("robot_id") or actual_msg.get("sender_id") or "UNKNOWN")

                if res_type == "POD" and res_id:
                    from app.services.reservations import release_pod
                    release_pod(str(res_id), releasing_bot)
                    self.log(f"[Tick {current_tick}] Released peer pod claim {res_id} by {releasing_bot}.")
                elif res_type == "CHARGER" and res_id:
                    from app.services.reservations import release_charger
                    release_charger((int(res_id[0]), int(res_id[1])), releasing_bot)
                    self.log(f"[Tick {current_tick}] Released peer charger claim {res_id} by {releasing_bot}.")

            elif m_type == "POD_SLOT_OCCUPANCY":
                pos_list = actual_msg.get("pos")
                occupant = actual_msg.get("occupant")
                src_bot = actual_msg.get("source_robot_id") or actual_msg.get("robot_id") or actual_msg.get("sender_id") or "PEER"
                if pos_list and hasattr(self.grid, "set_pod_slot_occupant"):
                    pos_tuple = (int(pos_list[0]), int(pos_list[1]))
                    self.grid.set_pod_slot_occupant(pos_tuple, occupant)
                    self.log(f"[Tick {current_tick}] Updated pod slot occupancy at {pos_tuple} -> {occupant} from peer {src_bot}.")

            elif m_type == "EMERGENCY_STOP":
                self.log(f"[Tick {current_tick}] Control command received: EMERGENCY_STOP.")
                self.fsm.transition(RobotEvent.E_STOP)
                self.robot.state = self.fsm.state
                self.pre_conflict_activity = None

            elif m_type in ("RESET", "RESET_FAILSAFE"):
                self.log(f"[Tick {current_tick}] Control command received: {m_type}.")
                self.reset_failsafe()

            elif m_type in ("TASK_REROUTE", "STATION_COMMAND"):
                target_robot = actual_msg.get("target_robot_id") or actual_msg.get("target_peer_id")
                if not target_robot or target_robot == self.robot.robot_id:
                    t_dict = actual_msg.get("task", {})
                    tid = t_dict.get("task_id")
                    if tid and self.fsm.state == RobotState.IDLE:
                        dropoff_pos = tuple(t_dict.get("dropoff", self.robot.position))
                        pickup_pos = tuple(t_dict.get("pickup", self.robot.position))
                        self._assign_initial_task(
                            goal_pos=dropoff_pos,
                            urgency=int(t_dict.get("urgency", 3)),
                            payload_weight_kg=float(t_dict.get("payload_weight_kg", 0.0)),
                            task_id=tid,
                            pickup_pos=pickup_pos,
                            task_type=t_dict.get("task_type", "STANDARD"),
                            target_shelf_id=t_dict.get("target_shelf_id"),
                            sku_to_pick=t_dict.get("sku_to_pick"),
                            quantity=int(t_dict.get("quantity", 1)),
                            destination_zone=t_dict.get("destination_zone"),
                            pick_station_id=t_dict.get("pick_station_id"),
                        )
                        self.log(f"[Tick {current_tick}] Executed authorized station directive {tid} from {sender}.")

            elif m_type == "STATION_HEARTBEAT":
                sender_station = actual_msg.get("station_id")
                sender_role = actual_msg.get("station_role")
                if sender_station == "AUTHORITY_STATION" or sender_role == "AUTHORITY_STATION":
                    self.authority_last_seen_tick = current_tick
                    if self.is_interim_coordinator or self.interim_coordinator_id is not None:
                        self.log(f"[Tick {current_tick}] AuthorityStation heartbeat received. Stepping down from interim coordinator.")
                        self.is_interim_coordinator = False
                        self.interim_coordinator_id = None

            elif m_type == "FAULT_ESCALATION":
                f_data = actual_msg.get("fault", {})
                fault_id = f_data.get("fault_id", "UNKNOWN")
                st_id = actual_msg.get("station_id", sender)
                if self.is_interim_coordinator:
                    resolution_record = {
                        "fault_id": fault_id,
                        "station_id": st_id,
                        "resolved_by": self.robot.robot_id,
                        "status": "APPROVED_BY_INTERIM_COORDINATOR",
                        "tick": current_tick,
                        "fault": f_data,
                    }
                    self.resolved_escalated_faults.append(resolution_record)
                    self.log(
                        f"[Tick {current_tick}] Interim Coordinator {self.robot.robot_id} "
                        f"approved escalated fault {fault_id} from {st_id}."
                    )
                else:
                    self.log(
                        f"[Tick {current_tick}] Received FAULT_ESCALATION {fault_id} from {st_id} "
                        f"(not interim coordinator, standing by)."
                    )

    def _update_interim_coordinator_election(self, current_tick: int) -> None:
        """
        ARCH-01: Elects a temporary interim coordinator among AMRs if AuthorityStation
        misses 5 consecutive heartbeats.
        Reuses the (priority_score, robot_id) deterministic tie-break already established for claims.
        Drops interim coordinator role immediately when AuthorityStation heartbeats resume.
        """
        authority_offline = (current_tick - self.authority_last_seen_tick) >= 5
        if not authority_offline:
            if self.is_interim_coordinator or self.interim_coordinator_id is not None:
                self.is_interim_coordinator = False
                self.interim_coordinator_id = None
            return

        my_p = float(self.robot.priority_score) if (self.task is not None and float(self.robot.priority_score) > -500.0) else 0.0
        candidates = [(my_p, str(self.robot.robot_id))]
        for p in self.peers.values():
            if p.last_seen_tick >= current_tick - 5:
                peer_p = float(p.priority_score) if float(p.priority_score) > -500.0 else 0.0
                candidates.append((peer_p, str(p.robot_id)))

        candidates.sort(key=lambda c: (-c[0], c[1]))
        winner_score, winner_id = candidates[0]

        was_interim = self.is_interim_coordinator
        self.interim_coordinator_id = winner_id
        self.is_interim_coordinator = (winner_id == self.robot.robot_id)

        if not was_interim and self.is_interim_coordinator:
            self.log(
                f"[Tick {current_tick}] AuthorityStation offline (missed {current_tick - self.authority_last_seen_tick} ticks). "
                f"Elected {self.robot.robot_id} as Interim Coordinator (priority={winner_score:.2f})."
            )

    def _resolve_contract_net_bids(self, current_tick: int) -> None:
        """
        Resolves pending contract-net bids after the bid window (2 ticks).
        The lowest bid wins. Ties broken by lowest robot_id string.
        Winning AMR self-assigns and broadcasts signed TASK_CLAIM; losers stand down.
        """
        for tid, bid_info in list(self.active_bids.items()):
            if bid_info.get("claimed"):
                continue

            ticks_elapsed = current_tick - bid_info["announced_tick"]
            # 2-tick window gives all network peers opportunity to submit bids
            if ticks_elapsed >= 2:
                all_bids = [(bid_info["my_bid"], self.robot.robot_id)] + [
                    (score, pid) for pid, score in bid_info["peer_bids"].items()
                ]
                all_bids.sort(key=lambda x: (x[0], x[1]))
                winning_score, winning_id = all_bids[0]

                bid_info["claimed"] = True
                self.known_task_claims.add(tid)

                if winning_id == self.robot.robot_id and self.fsm.state == RobotState.IDLE and not self.task:
                    t_dict = bid_info["task_dict"]
                    pickup_pos = tuple(t_dict["pickup"]) if "pickup" in t_dict else tuple(self.robot.position)
                    dropoff_pos = tuple(t_dict["dropoff"])
                    self._assign_initial_task(
                        goal_pos=dropoff_pos,
                        urgency=int(t_dict.get("urgency", 3)),
                        payload_weight_kg=float(t_dict.get("payload_weight_kg", 0.0)),
                        task_id=tid,
                        pickup_pos=pickup_pos,
                        task_type=t_dict.get("task_type", "STANDARD"),
                        target_shelf_id=t_dict.get("target_shelf_id"),
                        sku_to_pick=t_dict.get("sku_to_pick"),
                        quantity=int(t_dict.get("quantity", 1)),
                        destination_zone=t_dict.get("destination_zone"),
                        pick_station_id=t_dict.get("pick_station_id"),
                    )
                    if self.task:
                        self.task.return_to_home = bool(t_dict.get("return_to_home", True))
                        self.task.home_slot = tuple(t_dict.get("home_slot", pickup_pos))

                    target_shelf = t_dict.get("target_shelf_id")
                    if target_shelf:
                        self.claim_pod_resource(target_shelf, current_tick)

                    self.log(
                        f"[Tick {current_tick}] CONTRACT-NET WON: Task {tid} claimed by self (bid={winning_score:.1f}). "
                        f"Broadcasting TASK_CLAIM."
                    )

                    # Establish initial lease
                    self.task_leases[tid] = {
                        "holder": self.robot.robot_id,
                        "expires_tick": current_tick + 40,
                        "bid_score": winning_score,
                    }

                    # Broadcast signed TASK_CLAIM with lease
                    self.seq += 1
                    claim_payload = {
                        "type": "TASK_CLAIM",
                        "sender_id": self.robot.robot_id,
                        "robot_id": self.robot.robot_id,
                        "task_id": tid,
                        "winner_id": self.robot.robot_id,
                        "bid_score": winning_score,
                        "lease_ticks": 40,
                        "tick": current_tick,
                    }
                    envelope = sign_payload(claim_payload, secret_key=self.secret_key, seq=self.seq)
                    for peer_id in self.peer_ports.keys():
                        if peer_id != self.robot.robot_id:
                            self.transport.send(peer_id, envelope)
                    if hasattr(self, "halow_transport") and self.halow_transport:
                        self.halow_transport.send("DASHBOARD", envelope)
                else:
                    self.log(
                        f"[Tick {current_tick}] CONTRACT-NET LOST: Task {tid} won by {winning_id} "
                        f"({winning_score:.1f} vs my {bid_info['my_bid']:.1f}). Standing down."
                    )




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
    enable_idle_audit: Optional[bool] = None,
    pause_event: Optional[mp.Event] = None,
    fleet_roster: Optional[Dict[str, str]] = None,
    start_event: Optional[mp.Event] = None,
    start_barrier: Optional[mp.Barrier] = None,
    auto_idle_audit: Optional[bool] = None,
    auto_consolidation: Optional[bool] = None,
    auto_transfer: Optional[bool] = None,
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
        auto_idle_audit=auto_idle_audit,
        auto_consolidation=auto_consolidation,
        auto_transfer=auto_transfer,
        fleet_roster=fleet_roster,
    )

    if start_barrier is not None:
        try:
            start_barrier.wait()
        except Exception:
            pass
    elif start_event is not None:
        start_event.wait()

    if stop_event.is_set():
        node.close()
        return

    start_time = time.time()
    tick = 0
    try:
        while not stop_event.is_set() and (max_ticks <= 0 or tick < max_ticks):
            if pause_event is not None and pause_event.is_set():
                time.sleep(0.2)
                start_time = time.time() - tick * tick_interval_s
                continue
            node.step(tick)
            tick += 1

            now = time.time()
            target_time = start_time + tick * tick_interval_s
            sleep_time = target_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                # Overrun: yield minimally to prevent CPU starvation while catching up to global clock
                time.sleep(0.0001)
    finally:
        node.close()

    node.log(f"Robot Process terminated after {tick} ticks. Exiting.")
