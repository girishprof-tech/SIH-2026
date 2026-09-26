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
from app.security.hmac_envelope import sign_payload, verify_envelope, build_inventory_update_envelope
from app.security.replay_guard import ReplayGuard
from app.services.degraded_mode import DegradedModeDetector
from app.services.audit_mission import AuditMission
from app.services.inventory_ledger import InventoryLedger
from app.models.inventory import ShelfRecord
from app.ml.priority_gnn import compute_priority
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
        ledger: Optional[InventoryLedger] = None,
        fleet_roster: Optional[Dict[str, str]] = None,
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
        self.grid = WarehouseGrid(obstacles=self.obstacles, width=30, height=30)
        self.local_reservations: Dict[Tuple[int, int, int], str] = {}
        self.HOLD = 30

        # 5. Deterministic Finite State Machine
        self.fsm = RobotFSM(RobotState.IDLE)
        self.pre_conflict_activity: Optional[RobotState] = None
        self.failsafe_hold_ticks = 0

        # 6. Mission & Task Management & Local Inventory Cache
        self.world = build_default_world()
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

        # 11. Learned Edge Priority Model (NumPy Inference)
        self.gnn_model = get_priority_gnn_model()

        # 12. Contract-Net Decentralized Task Bidding
        self.active_bids: Dict[str, Dict[str, Any]] = {}
        self.known_task_claims: Set[str] = set()

        # 13. SORTING AMR Batch & Chute Occupancy Tracking
        self.carrying_batch: List[Dict[str, Any]] = []
        self.chute_occupancy: Dict[str, int] = {c_id: 0 for c_id in self.world.sortation_chutes}
        self.chute_full_threshold: int = 5

        # If goal_pos provided at startup, auto-initialize initial task for legacy/demo scenarios
        if self.goal_pos is not None:
            self._assign_initial_task(self.goal_pos, self.urgency)

    def trigger_autonomous_consolidation(self, chute_id: str, current_tick: int) -> Optional[Task]:
        """
        Autonomously spawns and broadcasts a CONSOLIDATE_EXPORT task when a sortation chute is full.
        """
        chute_info = self.world.sortation_chutes.get(chute_id)
        if not chute_info:
            return None

        chute_pos = (chute_info["x"], chute_info["y"])
        export_dock_pos = (29, 14)  # East perimeter export dock

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

    def decant_batch_item(self, item: Dict[str, Any], tick: int) -> str:
        """
        Decants a single item into the appropriate sortation chute based on its destination zone.
        """
        dest_zone = item.get("destination_zone", "OVERFLOW")
        chute_id = self.world.chute_for_destination(dest_zone)
        self.chute_occupancy[chute_id] = self.chute_occupancy.get(chute_id, 0) + 1
        self.log(f"[Tick {tick}] SORTING Robot decanted item {item.get('item_id', 'ITEM')} (dest={dest_zone}) into {chute_id} (count={self.chute_occupancy[chute_id]}).")

        # Check full threshold
        if self.chute_occupancy[chute_id] >= self.chute_full_threshold:
            self.trigger_autonomous_consolidation(chute_id, tick)

        return chute_id

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
            from app.services.reservations import release_robot_pod_claims
            release_robot_pod_claims(self.robot_id)
            if hasattr(self, "grid") and hasattr(self.grid, "set_pod_slot_occupant") and hasattr(self, "robot"):
                self.grid.set_pod_slot_occupant(self.robot.position, None)
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
        )
        self.halow_transport.send("DASHBOARD", halow_env)
        self.log(f"[Tick {current_tick}] Broadcasted INVENTORY_UPDATE for {shelf_id} (count={total_boxes}) over Mesh + HaLow.")


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
        self.robot.priority_score = compute_priority(
            self.robot, self.task, dist_to_goal, gnn_model=self.gnn_model
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
        self._resolve_contract_net_bids(tick)

        # Phase 1.5 Fix 3: Flush pending HaLow outbound queue every tick
        if hasattr(self, "halow_transport") and self.halow_transport is not None:
            self.halow_transport.flush_pending()

        # Phase 1.5 Fix 1: Prune stale pod claims & renew active claims
        from app.services.reservations import prune_stale_pod_claims, renew_pod_claim
        prune_stale_pod_claims(current_tick=tick)
        if self.robot.carrying_pod_id:
            renew_pod_claim(self.robot.carrying_pod_id, self.robot.robot_id, current_tick=tick)
        elif self.task and getattr(self.task, "target_shelf_id", None) and self.fsm.state in (RobotState.EN_ROUTE_PICKUP, RobotState.LIFTING):
            renew_pod_claim(self.task.target_shelf_id, self.robot.robot_id, current_tick=tick)

        # 2.1. Periodic Anti-Entropy Gossip & Ledger Resync (Every 20 ticks)
        if tick % 20 == 0 and self.inventory_ledger is not None:
            for s in self.inventory_ledger.get_all_shelves(current_tick=tick):
                local = self.local_inventory_cache.get(s.shelf_id)
                if local is None or s.last_audited_tick > local.last_audited_tick:
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
                (p.intended_pos if p.last_seen_tick < tick else p.position)
                for p in self.peers.values() if p.robot_id != self.robot.robot_id
            }
            peer_intents = {
                p.intended_pos
                for p in self.peers.values() if p.robot_id != self.robot.robot_id
            }

            # If idle robot is sitting inside a dock bay, only vacate if an active robot is targeting it
            dock_targeted = any(
                (p.intended_pos == (rx, ry) or any(s.get("x") == rx and s.get("y") == ry for s in (p.path or [])))
                for p in self.peers.values()
                if p.robot_id != self.robot.robot_id and p.state != "IDLE"
            )
            if ((rx, ry) in self.dropoff_stations or (rx, ry) in self.pickup_stations) and dock_targeted:
                clear_candidates = [(rx - 1, ry), (rx + 1, ry), (rx, ry - 1), (rx, ry + 1)]
                for cand in clear_candidates:
                    if 0 <= cand[0] < self.grid.width and 0 <= cand[1] < self.grid.height:
                        if self.grid.is_free(cand) and cand not in peer_positions and cand not in peer_intents and cand not in self.dropoff_stations and cand not in self.pickup_stations:
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
                    # Phase 1.5 Fix 1: Atomically claim shelf before lifting
                    from app.services.reservations import claim_pod
                    if not claim_pod(shelf_id, self.robot.robot_id, current_tick=tick):
                        self.log(f"[Tick {tick}] POD CLAIM REJECTED: Shelf {shelf_id} is already claimed by another robot. Transitioning to FAILSAFE_HOLD.")
                        self.fsm.state = RobotState.FAILSAFE_HOLD
                        self.robot.state = self.fsm.state
                        return self._build_telemetry_frame(tick, "POD_CLAIM_REJECTED", None)

                    self.robot.carrying_pod_id = shelf_id
                    if hasattr(self.grid, "set_pod_slot_occupant"):
                        self.grid.set_pod_slot_occupant(self.robot.position, self.robot.robot_id)
                    shelf_rec = self.local_inventory_cache.get(shelf_id) or self.inventory_ledger.get_shelf(shelf_id)
                    if shelf_rec:
                        self.robot.carrying_sku_manifest = dict(shelf_rec.sku_manifest)
                    self.log(f"[Tick {tick}] G2P Robot lifted pod {shelf_id} at {self.robot.position}.")

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
                    reserve_path(p_drop, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
            self.robot.state = self.fsm.state
            return self._build_telemetry_frame(tick, "LIFTING_COMPLETE", None)

        if self.fsm.state in (RobotState.DROPPING, RobotState.LOWERING):
            self.log(f"[Tick {tick}] Executing dropoff/lowering at {self.robot.position}...")
            
            # If carrying pod, handle item pick decrement and lower pod
            if self.robot.carrying_pod_id:
                pod_id = self.robot.carrying_pod_id
                sku_to_pick = getattr(self.task, "sku_to_pick", None) if self.task else None
                qty = getattr(self.task, "quantity", 1) if self.task else 1
                
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

                # Phase 1.5 Fix 1: Release pod claim and clear slot occupancy
                from app.services.reservations import release_pod
                release_pod(pod_id, self.robot.robot_id)
                if hasattr(self.grid, "set_pod_slot_occupant"):
                    self.grid.set_pod_slot_occupant(self.robot.position, None)

                self.robot.carrying_pod_id = None
                self.robot.carrying_sku_manifest = {}


            if self.task:
                self.completed_task_ids.add(self.task.task_id)
                self.task.status = "COMPLETED"
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
        max_peer_wait = 0.0 if self.tick_interval_s <= 0.0 else max(0.08, self.tick_interval_s * 0.8)
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
                if peer_snap.last_seen_tick < tick - 2:
                    continue

                # Current physical position of the peer: if peer's report was from a prior tick,
                # the peer already completed its move to intended_pos.
                px, py = peer_snap.intended_pos if peer_snap.last_seen_tick < tick else peer_snap.position
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
                                p_c = p_other.intended_pos if p_other.last_seen_tick < tick else p_other.position
                                for dt in range(30):
                                    self.local_reservations[(p_c[0], p_c[1], tick + dt)] = p_other.robot_id
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
                            (p.intended_pos if p.last_seen_tick < tick else p.position)
                            for p in self.peers.values() if p.robot_id != self.robot.robot_id
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
                            if dx_rel != 0:
                                candidate_evasions = [(rx, ry - 1), (rx, ry + 1), (rx - dx_rel, ry), (rx + dx_rel, ry)]
                            else:
                                candidate_evasions = [(rx - 1, ry), (rx + 1, ry), (rx, ry - dy_rel), (rx, ry + dy_rel)]

                            peer_blocked = {
                                (px, py), (pix, piy),
                                *((int(s["x"]), int(s["y"])) for s in peer_snap.path[:5])
                            }
                            other_peer_positions = {
                                (p.intended_pos if p.last_seen_tick < tick else p.position)
                                for p in self.peers.values() if p.robot_id != self.robot.robot_id
                            } | {
                                p.intended_pos for p in self.peers.values() if p.robot_id != self.robot.robot_id
                            }
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
                        # If winner's intended target cell is currently physically occupied or already committed by peer:
                        target_occupied = (intended_pos == (px, py)) or (intended_pos == (pix, piy) and peer_snap.robot_id < self.robot.robot_id)
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
                                        p_c = p_other.intended_pos if p_other.last_seen_tick < tick else p_other.position
                                        for dt in range(30):
                                            self.local_reservations[(p_c[0], p_c[1], tick + dt)] = p_other.robot_id
                                            self.local_reservations[(p_other.intended_pos[0], p_other.intended_pos[1], tick + dt)] = p_other.robot_id

                                all_blocked_cells_w = {
                                    (p.intended_pos if p.last_seen_tick < tick else p.position)
                                    for p in self.peers.values() if p.robot_id != self.robot.robot_id
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
                                        if next_w != self.robot.position and next_w not in all_blocked_cells_w:
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

        # 11.5. Broadcast Updated Intention if arbitration or throttling altered intended_pos
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
                action_taken = "IDLE"
            elif len(self.robot.path) > 1 and (int(self.robot.path[1]["x"]), int(self.robot.path[1]["y"])) == prev_pos:
                # Path contains a turn/orientation step at the current position: advance path
                self.robot.path = self.robot.path[1:]
                action_taken = "TURNED"
                self.consecutive_wait_ticks = 0
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
                # Phase 1.5 Fix 1: Atomically verify pod claim before transitioning into PICKING/LIFTING
                if self.robot_type == "GOODS_TO_PERSON":
                    shelf_id = getattr(self.task, "target_shelf_id", None) or self.world.shelf_at(self.robot.position[0], self.robot.position[1])
                    if not shelf_id:
                        nearby = [sid for sid, pos in self.world.pod_slots.items() if abs(pos[0] - self.robot.position[0]) + abs(pos[1] - self.robot.position[1]) <= 1]
                        if nearby:
                            shelf_id = nearby[0]
                    if shelf_id:
                        from app.services.reservations import claim_pod
                        if not claim_pod(shelf_id, self.robot.robot_id, current_tick=tick):
                            self.log(f"[Tick {tick}] POD CLAIM REJECTED at pickup: {shelf_id} already claimed by another robot. Transitioning to FAILSAFE_HOLD.")
                            self.fsm.state = RobotState.FAILSAFE_HOLD
                            self.robot.state = self.fsm.state
                            return self._build_telemetry_frame(tick, "POD_CLAIM_CONFLICT", None)
                        if hasattr(self.grid, "set_pod_slot_occupant"):
                            self.grid.set_pod_slot_occupant(self.robot.position, self.robot.robot_id)

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
            "path": [{"x": p["x"], "y": p["y"]} for p in self.robot.path[:8]],
            "goal": list(self.goal_pos) if self.goal_pos else list(self.robot.position),
            "conflict": conflict,
            "local_inventory_count": len(self.local_inventory_cache),
            "carrying_pod_id": self.robot.carrying_pod_id,
            "halow_status": self.halow_transport.get_status() if hasattr(self, "halow_transport") else None,
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
                sender = payload.get("sender_id") or payload.get("robot_id") or payload.get("bidder_id") or payload.get("winner_id") or body.get("sender_id", "unknown")
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

                peer_rt = actual_msg.get("robot_type")
                if peer_rt:
                    self.fleet_roster[sender_id] = peer_rt

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
                )
                self.peers[sender_id] = snap

                # Update local reservation table
                for k in [k for k, v in list(self.local_reservations.items()) if v == sender_id]:
                    del self.local_reservations[k]
                for p in snap.path:
                    self.local_reservations[(int(p["x"]), int(p["y"]), int(p["t"]))] = sender_id

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
                        )
                        self.log(f"[Tick {current_tick}] Accepted TASK_ASSIGNMENT {tid} to pickup {pickup_pos} -> dropoff {dropoff_pos}.")

            elif m_type == "TASK_ANNOUNCEMENT":
                # Handle decentralized contract-net announcement broadcast
                t_dict = actual_msg.get("task", {})
                tid = t_dict.get("task_id")
                task_type_str = t_dict.get("task_type", "STANDARD")

                # Explicit decoupled robot eligibility matching
                if task_type_str in ("INDUCT_BATCH", "DECANT_TO_CHUTE", "CONSOLIDATE_EXPORT"):
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


                if (
                    tid
                    and tid not in self.completed_task_ids
                    and tid not in self.known_task_claims
                    and tid not in self.active_bids
                    and self.fsm.state == RobotState.IDLE
                    and not self.task
                    and is_eligible
                ):
                    pickup_pos = tuple(t_dict["pickup"]) if "pickup" in t_dict else tuple(self.robot.position)
                    dist_to_pickup = abs(self.robot.position[0] - pickup_pos[0]) + abs(self.robot.position[1] - pickup_pos[1])
                    battery_penalty = max(0.0, (100.0 - self.robot.battery_pct) * 0.5)
                    bid_score = float(dist_to_pickup * 10.0 + battery_penalty)

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
                self.known_task_claims.add(tid)
                if tid in self.active_bids:
                    self.active_bids[tid]["claimed"] = True
                    if winner != self.robot.robot_id:
                        self.log(f"[Tick {current_tick}] Contract-Net: Peer {winner} claimed task {tid}. Standing down.")

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
                    )
                    self.local_inventory_cache[shelf_id] = rec
                    self.log(f"[Tick {current_tick}] Decentralized INVENTORY_UPDATE applied for {shelf_id} (count={box_count}, conf={conf:.2f}) from peer {src_bot}.")

            elif m_type == "EMERGENCY_STOP":
                self.log(f"[Tick {current_tick}] Control command received: EMERGENCY_STOP.")
                self.fsm.transition(RobotEvent.E_STOP)
                self.robot.state = self.fsm.state
                self.pre_conflict_activity = None

            elif m_type in ("RESET", "RESET_FAILSAFE"):
                self.log(f"[Tick {current_tick}] Control command received: {m_type}.")
                self.reset_failsafe()

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
                    )
                    target_shelf = t_dict.get("target_shelf_id")
                    if target_shelf:
                        from app.services.reservations import claim_pod
                        claim_pod(target_shelf, self.robot.robot_id, current_tick=current_tick)

                    self.log(
                        f"[Tick {current_tick}] CONTRACT-NET WON: Task {tid} claimed by self (bid={winning_score:.1f}). "
                        f"Broadcasting TASK_CLAIM."
                    )

                    # Broadcast signed TASK_CLAIM
                    self.seq += 1
                    claim_payload = {
                        "type": "TASK_CLAIM",
                        "sender_id": self.robot.robot_id,
                        "robot_id": self.robot.robot_id,
                        "task_id": tid,
                        "winner_id": self.robot.robot_id,
                        "bid_score": winning_score,
                        "tick": current_tick,
                    }
                    envelope = sign_payload(claim_payload, secret_key=self.secret_key, seq=self.seq)
                    for peer_id in self.peer_ports.keys():
                        if peer_id != self.robot.robot_id:
                            self.transport.send(peer_id, envelope)
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
    enable_idle_audit: bool = True,
    pause_event: Optional[mp.Event] = None,
    fleet_roster: Optional[Dict[str, str]] = None,
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
        fleet_roster=fleet_roster,
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
