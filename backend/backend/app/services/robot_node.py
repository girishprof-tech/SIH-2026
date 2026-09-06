"""
robot_node.py — Decentralized Autonomous Robot Execution Unit.

Each robot runs in its OWN independent OS process (via multiprocessing.Process).
It does NOT wait for a central dispatcher.
It:
  1. Computes its own priority score using Member 3's formula.
  2. Broadcasts its planned reservation claims directly to peers via multiprocessing.Queue.
  3. Receives peer claims, detects local spatial & swap conflicts using detect_peer_conflict().
  4. Runs peer arbitration using resolve_peer_conflict().
  5. Yields/brakes or replans for itself using real Member 2 find_path().
  6. Moves, turns, and updates battery independently.
  7. Logs every event, decision, and arbitration to its own logs/robot_{robot_id}.log.
  8. Pushes telemetry to the shared telemetry queue for the FastAPI viewer.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Setup import paths
ROOT_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from grid import WarehouseGrid
from pathfinder import find_path
from reservations import reserve_path, release_reservations, prune_past
from priority import calculate_priority_score
from conflict_detector import detect_peer_conflict
from arbitration import resolve_peer_conflict
from models import Heading, Robot, RobotState, Task


@dataclass
class PeerSnapshot:
    """Lightweight representation of a peer robot received via peer-to-peer message."""
    robot_id: str
    position: Tuple[int, int]
    heading: Heading
    priority_score: float
    state: RobotState
    wait_ticks_so_far: int
    path: List[Dict[str, Any]]
    last_seen_tick: int


class RobotNode:
    """
    Independent autonomous robot decision agent.
    Runs in its own process.
    """

    def __init__(
        self,
        robot_id: str,
        start_pos: Tuple[int, int],
        goal_pos: Tuple[int, int],
        urgency: int,
        battery_pct: float,
        obstacles: List[Tuple[int, int]],
        inbox: mp.Queue,
        peer_mailboxes: Dict[str, mp.Queue],
        telemetry_queue: Optional[mp.Queue] = None,
        log_dir: Optional[Path] = None,
        tick_interval_s: float = 0.1,
    ) -> None:
        self.robot_id = robot_id
        self.start_pos = start_pos
        self.goal_pos = goal_pos
        self.urgency = urgency
        self.battery_pct = battery_pct
        self.obstacles = obstacles
        self.inbox = inbox
        self.peer_mailboxes = peer_mailboxes
        self.telemetry_queue = telemetry_queue
        self.tick_interval_s = tick_interval_s

        # Initialize local logging
        if log_dir is None:
            log_dir = ROOT_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / f"robot_{robot_id}.log"
        self._setup_logger()

        # Instantiate local WarehouseGrid for local pathfinding
        self.grid = WarehouseGrid(obstacles=self.obstacles, width=30, height=30)

        # Initialize local Task & Robot models
        self.task_id = f"TASK-{robot_id}"
        self.task = Task(
            task_id=self.task_id,
            pickup=start_pos,
            dropoff=goal_pos,
            urgency=urgency,
            created_tick=0,
            assigned_robot_id=robot_id,
            status="IN_PROGRESS",
        )
        self.robot = Robot(
            robot_id=robot_id,
            position=start_pos,
            heading=Heading.NORTH,
            state=RobotState.EN_ROUTE,
            battery_pct=battery_pct,
            current_task_id=self.task_id,
            path=[],
            priority_score=0.0,
            wait_ticks_so_far=0,
            last_updated_tick=0,
        )

        # Local perceived state of peers: {peer_id: PeerSnapshot}
        self.peers: Dict[str, PeerSnapshot] = {}

        # Local reservation table (holds own reservations + perceived peer claims)
        self.local_reservations: Dict[Tuple[int, int, int], str] = {}
        self.HOLD = 30

        # Plan initial path
        self._plan_initial_path()

    def _setup_logger(self) -> None:
        """Sets up a dedicated file logger for this robot."""
        self.logger = logging.getLogger(f"RobotNode.{self.robot_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        fh = logging.FileHandler(self.log_file, mode="w", encoding="utf-8")
        formatter = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)
        self.log(f"Autonomous Robot Node initialized. PID={os.getpid()}, Start={self.start_pos}, Goal={self.goal_pos}, Urgency={self.urgency}")

    def log(self, message: str) -> None:
        self.logger.info(message)

    def _plan_initial_path(self) -> None:
        """Plans initial trajectory from start to goal."""
        path = find_path(
            start=self.robot.position,
            goal=self.goal_pos,
            current_tick=0,
            reservation_table=self.local_reservations,
            robot_id=self.robot.robot_id,
            grid=self.grid,
        )
        if path:
            self.robot.path = path
            reserve_path(path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
            self.log(f"Initial path planned: {len(path)} steps to goal {self.goal_pos}.")
        else:
            self.robot.path = [{"x": self.robot.position[0], "y": self.robot.position[1], "t": 0}]
            reserve_path(self.robot.path, self.robot.robot_id, self.local_reservations, hold_ticks_at_goal=self.HOLD)
            self.log(f"Warning: No initial path found. Holding at {self.robot.position}.")

    def _pathfinder_callback(self, start, goal, cur_tick, res_table, robot_id=None, **kwargs):
        return find_path(
            start=start,
            goal=goal,
            current_tick=cur_tick,
            reservation_table=res_table,
            robot_id=robot_id or self.robot.robot_id,
            grid=self.grid,
        )

    def step(self, tick: int) -> Dict[str, Any]:
        """
        Executes one autonomous tick loop step for this robot.
        """
        prev_pos = self.robot.position
        prev_heading = self.robot.heading

        # 1. Update priority score
        dist = self.robot.distance_to_goal()
        self.robot.priority_score = calculate_priority_score(self.robot, self.task, dist)

        # 2. Broadcast reservation claim to all peer robots directly
        claim_payload = {
            "type": "RESERVATION_CLAIM",
            "robot_id": self.robot.robot_id,
            "tick": tick,
            "position": self.robot.position,
            "heading": self.robot.heading,
            "priority_score": self.robot.priority_score,
            "state": self.robot.state,
            "wait_ticks": self.robot.wait_ticks_so_far,
            "path": list(self.robot.path[:6]),
        }
        for peer_id, mailbox in self.peer_mailboxes.items():
            if peer_id != self.robot.robot_id:
                try:
                    mailbox.put_nowait(claim_payload)
                except Exception:
                    pass

        # 3. Read incoming peer messages from inbox
        while not self.inbox.empty():
            try:
                msg = self.inbox.get_nowait()
            except Exception:
                break

            m_type = msg.get("type")
            if m_type == "RESERVATION_CLAIM":
                sender_id = msg["robot_id"]
                snap = PeerSnapshot(
                    robot_id=sender_id,
                    position=msg["position"],
                    heading=msg["heading"],
                    priority_score=msg["priority_score"],
                    state=msg["state"],
                    wait_ticks_so_far=msg["wait_ticks"],
                    path=msg["path"],
                    last_seen_tick=msg["tick"],
                )
                self.peers[sender_id] = snap

                # Update local reservation table with peer's claimed steps
                # Purge old reservations for this peer first
                for k in [k for k, v in self.local_reservations.items() if v == sender_id]:
                    del self.local_reservations[k]
                for p in snap.path:
                    self.local_reservations[(p["x"], p["y"], p["t"])] = sender_id

        # 4. Filter nearby peers (Manhattan distance <= 2)
        rx, ry = self.robot.position
        nearby_peers: List[PeerSnapshot] = []
        for snap in self.peers.values():
            px, py = snap.position
            if abs(rx - px) + abs(ry - py) <= 2 and snap.last_seen_tick >= tick - 2:
                nearby_peers.append(snap)

        # 5. Peer-to-Peer Conflict Detection & Arbitration
        action_taken = "MOVED"
        conflict_resolved = None

        if self.robot.state != RobotState.IDLE:
            for peer_snap in nearby_peers:
                # Wrap peer snapshot into Robot proxy for detector/arbitration
                peer_proxy = Robot(
                    robot_id=peer_snap.robot_id,
                    position=peer_snap.position,
                    heading=peer_snap.heading,
                    state=peer_snap.state,
                    battery_pct=80.0,
                    current_task_id=f"TASK-{peer_snap.robot_id}",
                    path=peer_snap.path,
                    priority_score=peer_snap.priority_score,
                    wait_ticks_so_far=peer_snap.wait_ticks_so_far,
                    last_updated_tick=peer_snap.last_seen_tick,
                )

                conflict = detect_peer_conflict(self.robot, peer_proxy, tick)
                if conflict:
                    c_cell = conflict["cell"]
                    c_type = conflict["type"]
                    self.log(
                        f"[Tick {tick}] CONFLICT DETECTED with {peer_snap.robot_id} ({c_type}) at cell ({c_cell['x']}, {c_cell['y']})! "
                        f"My Priority={self.robot.priority_score:.1f}, Peer Priority={peer_snap.priority_score:.1f}"
                    )

                    resolution = resolve_peer_conflict(
                        conflict=conflict,
                        robot_a=self.robot,
                        robot_b=peer_proxy,
                        reservation_table=self.local_reservations,
                        find_path_fn=self._pathfinder_callback,
                        tasks={self.task_id: self.task},
                    )
                    conflict_resolved = resolution

                    if resolution["loser_id"] == self.robot.robot_id:
                        action_taken = "YIELDED / BRAKED"
                        self.log(
                            f"[Tick {tick}] ARBITRATION RESULT: LOST to {peer_snap.robot_id}. "
                            f"Action=YIELD. Yielded right-of-way, incremented wait_ticks={self.robot.wait_ticks_so_far}."
                        )
                    else:
                        self.log(
                            f"[Tick {tick}] ARBITRATION RESULT: WON against {peer_snap.robot_id}. "
                            f"Action=PROCEED. Maintaining assigned trajectory."
                        )

        # 6. Execute Movement / Turn / Wait
        if self.robot.state == RobotState.IDLE or not self.robot.path:
            action_taken = "IDLE"
        elif action_taken == "YIELDED / BRAKED":
            # Robot yielded: hold position for this tick
            pass
        elif len(self.robot.path) > 1 and self.robot.path[1]["t"] == tick + 1:
            next_step = self.robot.path[1]
            next_pos = (next_step["x"], next_step["y"])

            dx = next_pos[0] - self.robot.position[0]
            dy = next_pos[1] - self.robot.position[1]
            if dx > 0: self.robot.heading = Heading.EAST
            elif dx < 0: self.robot.heading = Heading.WEST
            elif dy > 0: self.robot.heading = Heading.SOUTH
            elif dy < 0: self.robot.heading = Heading.NORTH

            if prev_heading != self.robot.heading and prev_pos == next_pos:
                action_taken = "TURNED"
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.5)
            else:
                action_taken = "MOVED"
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 1.0)

            self.robot.position = next_pos
            self.robot.path = self.robot.path[1:]
            self.robot.wait_ticks_so_far = 0
            self.robot.state = RobotState.EN_ROUTE
        else:
            self.robot.wait_ticks_so_far += 1
            action_taken = "WAITING"
            self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.1)

        self.robot.last_updated_tick = tick

        # Check task completion
        if self.robot.position == self.goal_pos:
            self.task.status = "COMPLETED"
            self.robot.state = RobotState.IDLE
            action_taken = "COMPLETED"
            self.robot.path = []
            self.log(f"[Tick {tick}] REACHED DESTINATION {self.goal_pos}! Mission COMPLETED.")

        # Prune local reservations
        prune_past(self.local_reservations, tick)

        self.log(
            f"[Tick {tick}] Pos={self.robot.position}, Heading={self.robot.heading.value}, "
            f"State={self.robot.state.value}, Action={action_taken}, Priority={self.robot.priority_score:.1f}, "
            f"Battery={self.robot.battery_pct:.1f}%, Waits={self.robot.wait_ticks_so_far}"
        )

        # 7. Prepare telemetry frame
        telemetry_frame = {
            "tick": tick,
            "robot_id": self.robot.robot_id,
            "x": self.robot.position[0],
            "y": self.robot.position[1],
            "heading": self.robot.heading.value,
            "state": self.robot.state.value,
            "battery_pct": round(self.robot.battery_pct, 1),
            "priority_score": round(self.robot.priority_score, 1),
            "urgency": self.urgency,
            "wait_ticks": self.robot.wait_ticks_so_far,
            "action": action_taken,
            "completed": self.task.status == "COMPLETED",
            "path": [{"x": p["x"], "y": p["y"]} for p in self.robot.path[:8]],
            "goal": list(self.goal_pos),
            "conflict": conflict_resolved,
        }

        if self.telemetry_queue is not None:
            try:
                self.telemetry_queue.put_nowait(telemetry_frame)
            except Exception:
                pass

        return telemetry_frame


def run_robot_process(
    robot_id: str,
    start_pos: Tuple[int, int],
    goal_pos: Tuple[int, int],
    urgency: int,
    battery_pct: float,
    obstacles: List[Tuple[int, int]],
    inbox: mp.Queue,
    peer_mailboxes: Dict[str, mp.Queue],
    telemetry_queue: mp.Queue,
    stop_event: mp.Event,
    log_dir_str: str,
    tick_interval_s: float = 0.1,
    max_ticks: int = 100,
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
        inbox=inbox,
        peer_mailboxes=peer_mailboxes,
        telemetry_queue=telemetry_queue,
        log_dir=Path(log_dir_str),
        tick_interval_s=tick_interval_s,
    )

    tick = 0
    while not stop_event.is_set() and tick < max_ticks:
        t0 = time.time()
        node.step(tick)
        tick += 1

        elapsed = time.time() - t0
        sleep_time = max(0.0, tick_interval_s - elapsed)
        time.sleep(sleep_time)

    node.log(f"Robot Process terminated after {tick} ticks. Exiting.")
