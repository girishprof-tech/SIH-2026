"""
station_node.py — Fixed-Infrastructure Station Nodes for Decentralized Warehouse Mesh.

Defines 3 fixed-infrastructure station types:
1. ImportStation: command scope strictly limited to import dock zone (INDUCT_BATCH, IN-1..3).
2. ExportStation: command scope strictly limited to export/sortation zone (CONSOLIDATE_EXPORT, OUT-1..3, CHUTES).
3. AuthorityStation: single point of authority for unresolvable escalations and emergency overrides,
   deliberately kept OUT of normal operational critical path (AMRs continue running if it fails).

Station nodes participate in the same HMAC-signed UDP peer mesh AMRs use, and expose a WiFi HaLow
channel (HaLowTransport) to the central dashboard for telemetry and command mirroring.
Station nodes hold NO heavy computation: no A* pathfinding, no FSM, no battery, and no state that
AMRs depend on to keep moving.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure imports resolve cleanly
ROOT_DIR = Path(__file__).resolve().parents[4]
for p in [
    ROOT_DIR / "backend" / "backend" / "app" / "services",
    ROOT_DIR / "backend" / "backend",
    ROOT_DIR / "backend" / "backend" / "app",
]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from app.security.hmac_envelope import DEFAULT_SECRET_KEY, sign_payload, verify_envelope
from app.security.replay_guard import ReplayGuard
from app.transport.halow_transport import HaLowTransport
from app.transport.udp_transport import UdpTransport

log = logging.getLogger(__name__)

DEFAULT_STATION_PORTS: Dict[str, int] = {
    "IMPORT_STATION": 9601,
    "EXPORT_STATION": 9602,
    "AUTHORITY_STATION": 9603,
}


class StationRole(str, Enum):
    IMPORT_STATION = "IMPORT_STATION"
    EXPORT_STATION = "EXPORT_STATION"
    AUTHORITY_STATION = "AUTHORITY_STATION"


class StationNode:
    """
    Fixed-infrastructure peer node in the decentralized warehouse mesh.

    Participates as an equal peer on the HMAC-signed UDP mesh and mirrors telemetry
    over the WiFi HaLow transport to the operator dashboard.
    """

    def __init__(
        self,
        station_id: str,
        role: StationRole | str,
        position: Tuple[int, int],
        port: int,
        peer_ports: Dict[str, int],
        secret_key: str = DEFAULT_SECRET_KEY,
        dashboard_port: int = 9099,
        host: str = "127.0.0.1",
        packet_loss_pct: float = 0.0,
        log_dir: Optional[Path] = None,
    ) -> None:
        self.station_id = station_id
        if isinstance(role, str):
            self.role = StationRole(role)
        else:
            self.role = role
        self.position = (int(position[0]), int(position[1]))
        self.port = port
        self.peer_ports = dict(peer_ports)
        self.secret_key = secret_key
        self.dashboard_port = dashboard_port
        self.host = host
        self.packet_loss_pct = packet_loss_pct
        self.log_dir = log_dir

        # Networking Transports:
        # 1. Peer-to-Peer UDP Transport (identical to AMR mesh)
        self.transport = UdpTransport(
            node_id=self.station_id,
            port=self.port,
            peer_ports=self.peer_ports,
            host=self.host,
            packet_loss_pct=self.packet_loss_pct,
        )

        # 2. Simulated WiFi HaLow Transport to Dashboard
        self.halow_transport = HaLowTransport(
            node_id=self.station_id,
            dashboard_port=self.dashboard_port,
            host=self.host,
            packet_loss_pct=self.packet_loss_pct,
        )

        # Security & State
        self.replay_guard = ReplayGuard(freshness_window_s=10.0)
        self.seq = 0
        self.current_tick = 0
        self.observed_robots: Dict[str, Dict[str, Any]] = {}
        self.rejection_log: List[Dict[str, Any]] = []
        self.fault_log: List[Dict[str, Any]] = []
        self.escalated_faults: List[Dict[str, Any]] = []
        self.is_authority_online: bool = True
        self.interim_escalation_enabled: bool = False

    def validate_command_scope(self, task_dict: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates whether this station has authority to issue or reroute the given task.

        Strict scope boundaries:
        - ImportStation: limited to INDUCT_BATCH or tasks sourced from IN-1/2/3 (import dock zone).
        - ExportStation: limited to CONSOLIDATE_EXPORT, TRANSFER_TO_SORTATION, DECANT_TO_CHUTE,
          or tasks targeted to OUT-1/2/3 / sortation chutes.
        - AuthorityStation: unrestricted (full command scope).
        """
        task_type = str(task_dict.get("task_type", "")).upper()
        tid = task_dict.get("task_id", "UNKNOWN")
        source_gate = str(task_dict.get("source_gate", "")).upper()
        destination_gate = str(task_dict.get("destination_gate", "")).upper()
        destination_zone = str(task_dict.get("destination_zone", "")).upper()

        pickup = task_dict.get("pickup")
        dropoff = task_dict.get("dropoff")

        if self.role == StationRole.IMPORT_STATION:
            # Check 1: Allowed task types for Import
            if task_type in ("INDUCT_BATCH", "IMPORT_UNLOAD"):
                return True, ""

            # Check 2: Task sourced from import dock gates
            if source_gate in ("IN-1", "IN-2", "IN-3"):
                return True, ""

            # Check 3: Pickup coordinates within West import dock boundary (x <= 2, 8 <= y <= 20)
            if pickup and isinstance(pickup, (list, tuple)) and len(pickup) >= 2:
                px, py = int(pickup[0]), int(pickup[1])
                if px <= 2 and 8 <= py <= 20:
                    return True, ""

            rejection_reason = (
                f"STATION_AUTHORITY_VIOLATION: ImportStation '{self.station_id}' rejected out-of-scope command "
                f"for task '{tid}' (type='{task_type}', source='{source_gate}', pickup={pickup}). "
                f"ImportStation can only issue/reroute INDUCT_BATCH or tasks sourced from West import dock (IN-1..3)."
            )
            return False, rejection_reason

        elif self.role == StationRole.EXPORT_STATION:
            # Check 1: Allowed task types for Export & Sortation
            if task_type in ("CONSOLIDATE_EXPORT", "TRANSFER_TO_SORTATION", "DECANT_TO_CHUTE"):
                return True, ""

            # Check 2: Destination is an outbound gate
            if destination_gate and (destination_gate.startswith("OUT") or (hasattr(self, "world") and destination_gate in self.world.export_gates)):
                return True, ""

            # Check 3: Dropoff coordinates within East export dock (x >= 27, 8 <= y <= 20)
            # or Sortation Section (21 <= x <= 27, 2 <= y <= 5)
            if dropoff and isinstance(dropoff, (list, tuple)) and len(dropoff) >= 2:
                dx, dy = int(dropoff[0]), int(dropoff[1])
                if (dx >= 27 and 8 <= dy <= 20) or (21 <= dx <= 27 and 2 <= dy <= 5):
                    return True, ""

            rejection_reason = (
                f"STATION_AUTHORITY_VIOLATION: ExportStation '{self.station_id}' rejected out-of-scope command "
                f"for task '{tid}' (type='{task_type}', destination='{destination_gate}', dropoff={dropoff}). "
                f"ExportStation can only issue/reroute CONSOLIDATE_EXPORT, TRANSFER_TO_SORTATION, DECANT_TO_CHUTE, "
                f"or tasks destined for East shipping dock (OUT-1..3) and Sortation Section."
            )
            return False, rejection_reason

        elif self.role == StationRole.AUTHORITY_STATION:
            # AuthorityStation has full command scope
            return True, ""

        return False, f"STATION_AUTHORITY_VIOLATION: Unknown station role '{self.role}'."

    def issue_task_command(
        self,
        command_type: str,
        task_dict: Dict[str, Any],
        target_peer_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Issues an authorized command to the mesh.

        Enforces scope at the station node, then cryptographic signs the envelope
        with `station_role` claim for receiving-end verification by AMRs.
        """
        is_valid, reason = self.validate_command_scope(task_dict)
        if not is_valid:
            self.rejection_log.append({
                "tick": self.current_tick,
                "timestamp": time.time(),
                "command_type": command_type,
                "task": task_dict,
                "reason": reason,
            })
            log.warning(f"[{self.station_id}] Command rejected: {reason}")
            return False, reason

        self.seq += 1
        payload = {
            "type": command_type,
            "station_role": self.role.value,
            "station_id": self.station_id,
            "station_pos": [self.position[0], self.position[1]],
            "task": task_dict,
            "tick": self.current_tick,
            "target_peer_id": target_peer_id,
        }
        env = sign_payload(payload, secret_key=self.secret_key, seq=self.seq)

        # 1. Mesh transmission
        if target_peer_id:
            self.transport.send(target_peer_id, env)
        else:
            for peer_id in self.peer_ports.keys():
                if peer_id != self.station_id:
                    self.transport.send(peer_id, env)

        # 2. Mirror to dashboard over WiFi HaLow
        halow_payload = dict(payload)
        halow_payload["channel"] = "HALOW"
        halow_env = sign_payload(halow_payload, secret_key=self.secret_key, seq=self.seq)
        self.halow_transport.send("DASHBOARD", halow_env)

        return True, f"Command '{command_type}' successfully issued by {self.station_id}."

    def report_fault(
        self,
        fault_id: str,
        description: str,
        severity: str = "HIGH",
        affected_node_id: Optional[str] = None,
        fallback_to_interim: bool = False,
    ) -> Dict[str, Any]:
        """
        Reports or escalates a fault.

        If Import or Export Station cannot resolve peer-to-peer, escalates to AuthorityStation.
        If AuthorityStation is offline/unreachable and fallback_to_interim is enabled,
        escalates to the AMR mesh / elected interim coordinator.
        Otherwise gracefully reports escalation unavailability WITHOUT hanging or blocking.
        """
        fault_record = {
            "fault_id": fault_id,
            "station_id": self.station_id,
            "station_role": self.role.value,
            "description": description,
            "severity": severity,
            "affected_node_id": affected_node_id,
            "tick": self.current_tick,
            "timestamp": time.time(),
        }
        self.fault_log.append(fault_record)

        if self.role == StationRole.AUTHORITY_STATION:
            self.escalated_faults.append(fault_record)
            return {
                "status": "RECORDED_AT_AUTHORITY",
                "escalated": False,
                "handled_locally": True,
                "fault": fault_record,
            }

        # For Import/Export stations, attempt escalation to Authority Station
        self.seq += 1
        escalation_payload = {
            "type": "FAULT_ESCALATION",
            "station_role": self.role.value,
            "station_id": self.station_id,
            "fault": fault_record,
            "tick": self.current_tick,
        }
        env = sign_payload(escalation_payload, secret_key=self.secret_key, seq=self.seq, sender_id=self.station_id)

        if not self.is_authority_online or "AUTHORITY_STATION" not in self.peer_ports:
            if fallback_to_interim or getattr(self, "interim_escalation_enabled", False):
                # Broadcast fault escalation to AMR peer mesh / interim coordinator
                for peer_id in self.peer_ports.keys():
                    if peer_id != self.station_id:
                        try:
                            self.transport.send(peer_id, env)
                        except Exception:
                            pass
                log.info(f"[{self.station_id}] AuthorityStation offline. Escalated fault {fault_id} to AMR peer mesh / interim coordinator.")
                return {
                    "status": "ESCALATED_TO_INTERIM_COORDINATOR",
                    "escalated": True,
                    "escalation_failed": False,
                    "reason": "AuthorityStation offline; escalated to AMR interim coordinator.",
                    "fault": fault_record,
                }

            # Clean degradation: Authority is down, station logs failure and continues operation
            log.warning(f"[{self.station_id}] AuthorityStation unreachable. Operating autonomously without escalation.")
            return {
                "status": "ESCALATION_UNAVAILABLE",
                "escalated": False,
                "escalation_failed": True,
                "reason": "AuthorityStation unreachable; operating autonomously without escalation.",
                "fault": fault_record,
            }

        try:
            self.transport.send("AUTHORITY_STATION", env)
            return {
                "status": "ESCALATED_TO_AUTHORITY",
                "escalated": True,
                "escalation_failed": False,
                "fault": fault_record,
            }
        except Exception as e:
            return {
                "status": "ESCALATION_ERROR",
                "escalated": False,
                "escalation_failed": True,
                "reason": f"Transport error sending to AuthorityStation: {e}",
                "fault": fault_record,
            }

    def escalate_fault_to_interim(
        self,
        fault_id: str,
        description: str,
        severity: str = "HIGH",
        affected_node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Explicitly escalates a fault to the AMR mesh / interim coordinator."""
        return self.report_fault(
            fault_id=fault_id,
            description=description,
            severity=severity,
            affected_node_id=affected_node_id,
            fallback_to_interim=True,
        )

    def poll_and_step(self, current_tick: int) -> Dict[str, Any]:
        """
        Executes one tick step: drains inbox, validates signatures, tracks zone robots.
        No pathfinding, no FSM, no battery depletion, no heavy computation.
        """
        self.current_tick = current_tick
        messages = self.transport.recv_all()

        for msg in messages:
            if "signature" in msg and "body" in msg:
                valid, payload, err = verify_envelope(msg, secret_key=self.secret_key)
                if not valid or not payload:
                    continue
                body = msg.get("body", {})
                sender = payload.get("sender_id") or payload.get("robot_id") or payload.get("station_id") or body.get("sender_id", "unknown")
                seq = body.get("seq")
                ts = body.get("timestamp", time.time())
                r_valid, _ = self.replay_guard.validate(sender, seq, ts)
                if not r_valid:
                    continue
                actual_msg = payload
            else:
                actual_msg = msg

            m_type = actual_msg.get("type")

            # Track robot telemetry in station's observation window
            if m_type in ("RESERVATION_CLAIM", "ROBOT_TELEMETRY", "HEARTBEAT"):
                rid = actual_msg.get("robot_id")
                if rid:
                    self.observed_robots[rid] = {
                        "position": actual_msg.get("position"),
                        "state": actual_msg.get("state"),
                        "task_id": actual_msg.get("task_id"),
                        "tick": current_tick,
                    }

            # Authority Station receives fault escalations from domain stations
            elif m_type == "FAULT_ESCALATION" and self.role == StationRole.AUTHORITY_STATION:
                f_data = actual_msg.get("fault", {})
                self.escalated_faults.append(f_data)
                log.info(f"[AuthorityStation] Received fault escalation from {actual_msg.get('station_id')}: {f_data.get('description')}")

        # AuthorityStation periodically broadcasts signed STATION_HEARTBEAT to all mesh peers
        if self.role == StationRole.AUTHORITY_STATION and self.is_authority_online:
            self.seq += 1
            hb_payload = {
                "type": "STATION_HEARTBEAT",
                "station_id": self.station_id,
                "station_role": self.role.value,
                "tick": current_tick,
            }
            hb_env = sign_payload(hb_payload, secret_key=self.secret_key, seq=self.seq, sender_id=self.station_id)
            for peer_id in self.peer_ports.keys():
                if peer_id != self.station_id:
                    try:
                        self.transport.send(peer_id, hb_env)
                    except Exception:
                        pass

        # Send station telemetry frame to dashboard over HaLow periodically (every 5 ticks)
        telemetry_frame = {
            "type": "STATION_TELEMETRY",
            "station_id": self.station_id,
            "role": self.role.value,
            "position": {"x": self.position[0], "y": self.position[1]},
            "tick": current_tick,
            "status": "ONLINE",
            "observed_robots_count": len(self.observed_robots),
            "rejections_count": len(self.rejection_log),
            "faults_count": len(self.fault_log),
            "escalated_count": len(self.escalated_faults),
        }
        if current_tick % 5 == 0:
            self.halow_transport.send("DASHBOARD", telemetry_frame)

        return telemetry_frame

    def close(self) -> None:
        """Closes all networking sockets cleanly."""
        try:
            self.transport.close()
        except Exception:
            pass
        try:
            self.halow_transport.close()
        except Exception:
            pass


def run_station_process(
    station_id: str,
    role: str,
    position: Tuple[int, int],
    port: int,
    peer_ports: Dict[str, int],
    stop_event: mp.Event,
    log_dir: Optional[str] = None,
    tick_interval_s: float = 0.15,
    dashboard_port: int = 9099,
    secret_key: str = DEFAULT_SECRET_KEY,
    start_event: Optional[mp.Event] = None,
    start_barrier: Optional[mp.Barrier] = None,
    pause_event: Optional[mp.Event] = None,
    speed_multiplier: Optional[Any] = None,
    **kwargs: Any,
) -> None:
    """
    Entrypoint function executed in an independent OS process for each StationNode.
    """
    l_path = Path(log_dir) if log_dir else None
    station = StationNode(
        station_id=station_id,
        role=role,
        position=position,
        port=port,
        peer_ports=peer_ports,
        secret_key=secret_key,
        dashboard_port=dashboard_port,
        log_dir=l_path,
    )
    if start_barrier is not None:
        try:
            start_barrier.wait(timeout=3.0)
        except Exception:
            pass
    if start_event is not None:
        try:
            start_event.wait(timeout=3.0)
        except Exception:
            pass

    if stop_event.is_set():
        station.close()
        return

    tick = 0
    try:
        while not stop_event.is_set():
            if pause_event is not None and pause_event.is_set():
                time.sleep(0.1)
                continue
            step_start = time.time()
            station.poll_and_step(tick)
            tick += 1
            step_duration = time.time() - step_start
            speed = float(speed_multiplier.value) if speed_multiplier is not None else 1.0
            effective_interval = tick_interval_s / max(0.1, min(speed, 10.0))
            sleep_time = effective_interval - step_duration
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                time.sleep(0.0005)
    finally:
        station.close()
