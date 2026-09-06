"""
robot_node.py — Decentralized Autonomous Robot Execution Unit.

This uses real UDP sockets over the loopback interface (127.0.0.1) for this single-machine demo.
Because this is standard UDP networking (not a Python-internal mechanism), the identical code
works unchanged if robots run on separate physical machines on the same LAN (e.g., over WiFi
Direct or a real WiFi network) — only the IP addresses would need to change from 127.0.0.1 to
each machine's real address.

Each robot runs in its OWN independent OS process (via multiprocessing.Process).
It does NOT wait for a central dispatcher.
It:
  1. Computes its own priority score using Member 3's formula.
  2. Broadcasts its planned reservation claims directly to peers via non-blocking UDP sockets.
  3. Receives peer claims, detects local spatial & swap conflicts using detect_peer_conflict().
  4. Runs peer arbitration using resolve_peer_conflict().
  5. Yields/brakes or replans for itself using real Member 2 find_path().
  6. Moves, turns, and updates battery independently.
  7. Logs every event, decision, and arbitration to its own logs/robot_{robot_id}.log.
  8. Pushes telemetry to the shared telemetry queue for the FastAPI viewer.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import socket
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
    intended_pos: Tuple[int, int]
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
        port: int,
        peer_ports: Dict[str, int],
        telemetry_queue: Optional[mp.Queue] = None,
        log_dir: Optional[Path] = None,
        tick_interval_s: float = 0.1,
        host: str = "127.0.0.1",
    ) -> None:
        self.robot_id = robot_id
        self.start_pos = start_pos
        self.goal_pos = goal_pos
        self.urgency = urgency
        self.battery_pct = battery_pct
        self.obstacles = obstacles
        self.port = port
        self.peer_ports = peer_ports
        self.host = host
        self.telemetry_queue = telemetry_queue
        self.tick_interval_s = tick_interval_s

        # Real UDP socket for decentralized peer-to-peer communication
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.setblocking(False)

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

    def close(self) -> None:
        """Closes the UDP socket."""
        try:
            self.sock.close()
        except Exception:
            pass

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
        Sequence:
          1. Update priority score.
          2. Compute candidate intended next position (not yet committed).
          3. Broadcast intended next position and reservation claim to all peers.
          4. Drain inbox and ensure messages for this tick from nearby peers are received.
          5. Symmetric peer conflict detection (vertex, swap, stationary occupancy).
          6. Deterministic priority arbitration (identical symmetric winner/loser decision).
          7. Winner proceeds (or waits if target cell is currently occupied by peer).
             Loser yields/brakes, increments wait_ticks, and replans around winner.
          8. Commit move only after conflict checks pass.
        """
        prev_pos = self.robot.position
        prev_heading = self.robot.heading

        # 1. Update priority score
        dist = self.robot.distance_to_goal()
        self.robot.priority_score = calculate_priority_score(self.robot, self.task, dist)

        # 2. Compute candidate INTENDED next position (not yet committed)
        if self.robot.state == RobotState.IDLE:
            intended_pos = self.robot.position
        elif not self.robot.path:
            # If path exhausted but not at goal, attempt replan
            if self.robot.position != self.goal_pos:
                re_path = find_path(
                    start=self.robot.position,
                    goal=self.goal_pos,
                    current_tick=tick,
                    reservation_table=self.local_reservations,
                    grid=self.grid,
                )
                if re_path and len(re_path) > 1:
                    self.robot.path = re_path
                    self.robot.state = RobotState.EN_ROUTE
                    intended_pos = (int(self.robot.path[1]["x"]), int(self.robot.path[1]["y"]))
                else:
                    intended_pos = self.robot.position
            else:
                intended_pos = self.robot.position
        elif len(self.robot.path) > 1:
            intended_pos = (int(self.robot.path[1]["x"]), int(self.robot.path[1]["y"]))
        else:
            intended_pos = self.robot.position

        # 3. Broadcast reservation claim & intended move to all peer robots via UDP
        claim_payload = {
            "type": "RESERVATION_CLAIM",
            "robot_id": self.robot.robot_id,
            "tick": tick,
            "position": [self.robot.position[0], self.robot.position[1]],
            "intended_pos": [intended_pos[0], intended_pos[1]],
            "heading": self.robot.heading.value if hasattr(self.robot.heading, "value") else str(self.robot.heading),
            "priority_score": self.robot.priority_score,
            "state": self.robot.state.value if hasattr(self.robot.state, "value") else str(self.robot.state),
            "wait_ticks": self.robot.wait_ticks_so_far,
            "path": list(self.robot.path[:8]),
        }
        data = json.dumps(claim_payload).encode("utf-8")
        for peer_id, peer_port in self.peer_ports.items():
            if peer_id != self.robot.robot_id:
                try:
                    self.sock.sendto(data, (self.host, peer_port))
                except Exception:
                    pass

        # 4. Drain inbox and ensure messages from nearby peers for this tick are received
        wait_start = time.time()
        max_peer_wait = max(0.12, self.tick_interval_s * 1.2)
        while (time.time() - wait_start) < max_peer_wait:
            self._drain_inbox(tick)

            # Check if any nearby peer (distance <= 3) is still on an older tick
            nearby_stale = False
            for peer_snap in self.peers.values():
                m_dist = abs(self.robot.position[0] - peer_snap.position[0]) + abs(self.robot.position[1] - peer_snap.position[1])
                if m_dist <= 3 and peer_snap.last_seen_tick < tick:
                    nearby_stale = True
                    break
            if not nearby_stale:
                break
            time.sleep(0.003)

        # Fail-safe: Check if any immediately adjacent peer (dist <= 2) is still unconfirmed for this tick
        unconfirmed_nearby = False
        for peer_snap in self.peers.values():
            m_dist = abs(self.robot.position[0] - peer_snap.position[0]) + abs(self.robot.position[1] - peer_snap.position[1])
            if m_dist <= 2 and peer_snap.last_seen_tick < tick:
                unconfirmed_nearby = True
                break

        if unconfirmed_nearby and intended_pos != self.robot.position:
            # Cannot safely move toward an unconfirmed adjacent robot; hold position this tick
            intended_pos = self.robot.position
            action_taken = "WAITING"
            self.log(f"[Tick {tick}] HOLDING POSITION: Peer nearby did not confirm tick {tick} position in time.")

        # 5. Peer-to-Peer Conflict Detection & Symmetric Arbitration
        action_taken = "MOVED"
        conflict_resolved = None

        if self.robot.state != RobotState.IDLE and intended_pos != self.robot.position:
            rx, ry = self.robot.position
            for peer_snap in list(self.peers.values()):
                if peer_snap.last_seen_tick < tick - 2:
                    continue  # ignore completely dead peers

                px, py = peer_snap.position
                pix, piy = peer_snap.intended_pos
                m_dist = abs(rx - px) + abs(ry - py)

                if m_dist > 2:
                    continue

                # Check conflict conditions:
                # 1) SWAP_CONFLICT: I intend to enter peer's current cell, while peer intends to enter my current cell
                is_swap = (intended_pos == (px, py) and (pix, piy) == (rx, ry) and (rx, ry) != (px, py))
                # 2) VERTEX_CONFLICT: Both intend to enter the exact same cell
                is_vertex = (intended_pos == (pix, piy) and intended_pos != (rx, ry))
                # 3) HEAD_ON / APPROACH: Peer is currently at intended_pos and either staying or approaching
                is_blocked = (intended_pos == (px, py) and (pix, piy) == (px, py))

                if is_swap or is_vertex or is_blocked:
                    c_type = "SWAP_CONFLICT" if is_swap else ("CELL_OVERLAP" if is_vertex else "STATIONARY_BLOCK")
                    conflict_cell = {"x": intended_pos[0], "y": intended_pos[1]}

                    # Deterministic, symmetric priority comparison
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
                        # THIS ROBOT IS THE LOSER
                        action_taken = "YIELDED / BRAKED"
                        self.robot.wait_ticks_so_far += 1
                        self.robot.state = RobotState.CONFLICT_NEGOTIATING
                        self.log(
                            f"[Tick {tick}] ARBITRATION RESULT: LOST to {peer_snap.robot_id}. "
                            f"Action=YIELD. Yielded right-of-way, incremented wait_ticks={self.robot.wait_ticks_so_far}."
                        )

                        # Purge stale reservations for self
                        for k in [k for k, v in list(self.local_reservations.items()) if v == self.robot.robot_id]:
                            del self.local_reservations[k]

                        re_path = find_path(
                            start=self.robot.position,
                            goal=self.goal_pos,
                            current_tick=tick,
                            reservation_table=self.local_reservations,
                            grid=self.grid,
                        )
                        if re_path and len(re_path) > 1:
                            self.robot.path = re_path
                            self.log(f"[Tick {tick}] Replanned alternate detour path ({len(re_path)} steps).")
                        else:
                            self.robot.path = [{"x": rx, "y": ry, "t": tick}, {"x": rx, "y": ry, "t": tick + 1}]

                        # Loser cancels intended move: stays at current position
                        intended_pos = self.robot.position
                        conflict_resolved = {
                            "winner_id": winner_id,
                            "loser_id": loser_id,
                            "action": "YIELD_AND_WAIT",
                            "type": c_type,
                            "cell": conflict_cell,
                        }
                        break
                    else:
                        # THIS ROBOT IS THE WINNER
                        self.log(
                            f"[Tick {tick}] ARBITRATION RESULT: WON against {peer_snap.robot_id}. "
                            f"Action=PROCEED. Maintaining assigned trajectory."
                        )
                        # If target cell is currently occupied by the peer (e.g. swap),
                        # winner holds for 1 tick so yielding peer can clear/turn
                        if intended_pos == (px, py):
                            intended_pos = self.robot.position
                            action_taken = "WAITING"
                            self.log(f"[Tick {tick}] Pausing 1 tick at {self.robot.position} for yielding peer {peer_snap.robot_id} to clear {px, py}.")

                        conflict_resolved = {
                            "winner_id": winner_id,
                            "loser_id": loser_id,
                            "action": "PROCEED",
                            "type": c_type,
                            "cell": conflict_cell,
                        }
                        break

        # 6. Execute Movement / Turn / Wait (Commit move only after all conflict checks pass)
        if self.robot.state == RobotState.IDLE or not self.robot.path:
            action_taken = "IDLE"
        elif intended_pos == prev_pos:
            # Robot yielded or held position
            if action_taken != "YIELDED / BRAKED":
                self.robot.wait_ticks_so_far += 1
                action_taken = "WAITING"
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.1)
        else:
            dx = intended_pos[0] - prev_pos[0]
            dy = intended_pos[1] - prev_pos[1]
            if dx > 0: self.robot.heading = Heading.EAST
            elif dx < 0: self.robot.heading = Heading.WEST
            elif dy > 0: self.robot.heading = Heading.SOUTH
            elif dy < 0: self.robot.heading = Heading.NORTH

            if prev_heading != self.robot.heading and prev_pos == intended_pos:
                action_taken = "TURNED"
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 0.5)
            else:
                action_taken = "MOVED"
                self.robot.battery_pct = max(0.0, self.robot.battery_pct - 1.0)

            self.robot.position = intended_pos
            self.robot.path = self.robot.path[1:]
            self.robot.wait_ticks_so_far = 0
            self.robot.state = RobotState.EN_ROUTE

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


    def _drain_inbox(self, current_tick: int) -> None:
        """Receives all pending UDP datagrams without blocking."""
        while True:
            try:
                raw_data, addr = self.sock.recvfrom(4096)
            except (BlockingIOError, socket.error):
                break
            try:
                msg = json.loads(raw_data.decode("utf-8"))
            except Exception:
                continue

            m_type = msg.get("type")
            if m_type == "RESERVATION_CLAIM":
                sender_id = msg["robot_id"]
                p_pos = tuple(msg["position"])
                p_intent = tuple(msg.get("intended_pos", msg["position"]))
                h_val = msg.get("heading", "NORTH")
                try:
                    h_enum = Heading(h_val)
                except Exception:
                    h_enum = Heading.NORTH
                s_val = msg.get("state", "EN_ROUTE")
                try:
                    s_enum = RobotState(s_val)
                except Exception:
                    s_enum = RobotState.EN_ROUTE

                snap = PeerSnapshot(
                    robot_id=sender_id,
                    position=(int(p_pos[0]), int(p_pos[1])),
                    intended_pos=(int(p_intent[0]), int(p_intent[1])),
                    heading=h_enum,
                    priority_score=float(msg["priority_score"]),
                    state=s_enum,
                    wait_ticks_so_far=int(msg["wait_ticks"]),
                    path=msg["path"],
                    last_seen_tick=int(msg["tick"]),
                )
                self.peers[sender_id] = snap

                # Update local reservation table
                for k in [k for k, v in list(self.local_reservations.items()) if v == sender_id]:
                    del self.local_reservations[k]
                for p in snap.path:
                    self.local_reservations[(int(p["x"]), int(p["y"]), int(p["t"]))] = sender_id


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
    )

    tick = 0
    try:
        while not stop_event.is_set() and tick < max_ticks:
            t0 = time.time()
            node.step(tick)
            tick += 1

            elapsed = time.time() - t0
            sleep_time = max(0.0, tick_interval_s - elapsed)
            time.sleep(sleep_time)
    finally:
        node.close()

    node.log(f"Robot Process terminated after {tick} ticks. Exiting.")
