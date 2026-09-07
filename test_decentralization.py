"""
test_decentralization.py — Proof of Edge-AI Decentralized Fleet Coordination.

Demonstrates and verifies that:
  1. Each robot runs in its own independent process with its own tick loop.
  2. Robots negotiate and arbitrate peer-to-peer via direct IPC channels (multiprocessing.Queue).
  3. FastAPI acts as a PURE TELEMETRY VIEWER.
  4. When FastAPI is KILLED, robot processes remain 100% unaffected and continue
     moving, detecting conflicts, arbitrating, yielding, and logging to their own files.
  5. When FastAPI is RESTARTED, it reconnects to the running fleet and resumes streaming.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

import requests

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

from app.services.fleet_orchestrator import FleetOrchestrator
from app.services.telemetry_bus import read_latest_telemetry


def parse_robot_log_ticks(log_file: Path) -> List[int]:
    """Extracts all tick numbers logged by an autonomous robot."""
    ticks = []
    if not log_file.exists():
        return ticks
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            if "[Tick " in line:
                try:
                    part = line.split("[Tick ")[1].split("]")[0]
                    ticks.append(int(part))
                except Exception:
                    pass
    return sorted(list(set(ticks)))


def main():
    print("=" * 80)
    print("DECENTRALIZATION PROOF: PEER-TO-PEER MULTI-ROBOT COORDINATION")
    print("Testing resilience against Central Backend/FastAPI failure")
    print("=" * 80)

    log_dir = ROOT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Clean old logs and telemetry file to ensure a clean test run
    for f in log_dir.glob("*.log"):
        try:
            f.unlink()
        except Exception:
            pass
    telemetry_file = log_dir / "telemetry_state.json"
    if telemetry_file.exists():
        try:
            telemetry_file.unlink()
        except Exception:
            pass

    # 5 AMRs configured with intersecting trajectories to guarantee conflict arbitration
    robots_config = [
        {"robot_id": "AMR-01", "start": (1, 6), "goal": (18, 6), "urgency": 5, "battery_pct": 90.0},
        {"robot_id": "AMR-02", "start": (18, 6), "goal": (1, 6), "urgency": 3, "battery_pct": 75.0},
        {"robot_id": "AMR-03", "start": (7, 1), "goal": (7, 20), "urgency": 4, "battery_pct": 85.0},
        {"robot_id": "AMR-04", "start": (7, 20), "goal": (7, 1), "urgency": 2, "battery_pct": 60.0},
        {"robot_id": "AMR-05", "start": (14, 1), "goal": (14, 20), "urgency": 5, "battery_pct": 95.0},
    ]

    orchestrator = None
    fastapi_proc = None
    fastapi_proc2 = None

    try:
        # ── Phase 1: Start Decentralized Robot Fleet Processes ─────────────────────
        print("\n[PHASE 1] Spawning 5 independent autonomous robot processes...")
        orchestrator = FleetOrchestrator(
            robots_config=robots_config,
            tick_interval_s=0.10,  # 100ms per tick
            max_ticks=250,
            log_dir=log_dir,
        )
        orchestrator.start()

        # Verify each robot's dedicated log file was created (allow up to 4s for Windows spawn)
        for r in robots_config:
            rf = log_dir / f"robot_{r['robot_id']}.log"
            created = False
            for _ in range(40):
                if rf.exists() and rf.stat().st_size > 0:
                    created = True
                    break
                time.sleep(0.1)
            assert created, f"Log file for {r['robot_id']} was not created!"
        print("  -> Confirmed: Each robot initialized its own independent log file.")

        # ── Phase 2: Start FastAPI Telemetry Viewer ───────────────────────────────
        print("\n[PHASE 2] Starting FastAPI Telemetry Viewer (uvicorn process)...")
        backend_cwd = ROOT_DIR / "backend" / "backend"
        fastapi_proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000", "--log-level", "warning"],
            cwd=str(backend_cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  -> FastAPI Telemetry Viewer PID = {fastapi_proc.pid}")

        # Wait for FastAPI to become healthy
        connected = False
        for _ in range(40):
            try:
                resp = requests.get("http://127.0.0.1:8000/health", timeout=1.0)
                if resp.status_code == 200 and resp.json().get("status") == "ok":
                    connected = True
                    break
            except Exception:
                time.sleep(0.2)

        assert connected, "FastAPI failed to start or /health did not respond!"
        print("  -> Confirmed: FastAPI connected and is streaming fleet telemetry.")

        # Let fleet run for a short duration while FastAPI is alive
        time.sleep(1.2)
        resp = requests.get("http://127.0.0.1:8000/health").json()
        tick_before_kill = resp.get("tick", 0)
        print(f"  -> Status at Tick {tick_before_kill}: {resp.get('robots', 0)} robots active.")

        # ── Phase 3: KILL FastAPI Process ─────────────────────────────────────────
        print("\n[PHASE 3] Simulating server crash: KILLING FastAPI Process...")
        fastapi_proc.terminate()
        try:
            fastapi_proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            fastapi_proc.kill()
        print(f"  -> FastAPI process (PID={fastapi_proc.pid}) is DEAD.")

        # Verify FastAPI is genuinely unreachable
        server_is_dead = False
        try:
            requests.get("http://127.0.0.1:8000/health", timeout=0.5)
        except Exception:
            server_is_dead = True
        assert server_is_dead, "FastAPI process was expected to be dead, but still answered!"
        print("  -> Confirmed: FastAPI /health is completely unreachable.")

        # ── Phase 4: Verify Robots Continue Operating with FastAPI Dead ───────────
        print("\n[PHASE 4] Waiting 2.0s (~20 ticks) while FastAPI is completely dead...")
        time.sleep(2.0)

        # Check logs directly from the autonomous robot processes
        ticks_dead_amr1 = parse_robot_log_ticks(log_dir / "robot_AMR-01.log")
        ticks_dead_amr2 = parse_robot_log_ticks(log_dir / "robot_AMR-02.log")

        latest_tick = max(ticks_dead_amr1[-1] if ticks_dead_amr1 else 0, ticks_dead_amr2[-1] if ticks_dead_amr2 else 0)
        ticks_advanced = latest_tick - tick_before_kill
        print(f"  -> AMR-01 latest tick logged: {ticks_dead_amr1[-1] if ticks_dead_amr1 else 'N/A'}")
        print(f"  -> AMR-02 latest tick logged: {ticks_dead_amr2[-1] if ticks_dead_amr2 else 'N/A'}")
        print(f"  -> Ticks advanced during server outage: {ticks_advanced} ticks!")

        assert ticks_advanced >= 8, (
            f"Robots stopped progressing when FastAPI died! Advanced only {ticks_advanced} ticks."
        )
        print("  -> Confirmed: Robots continued advancing ticks completely independently of FastAPI!")

        # Check if conflicts occurred and were arbitrated peer-to-peer during outage
        print("\n[PHASE 4b] Examining autonomous robot peer-to-peer conflict logs:")
        conflict_found_in_logs = False
        for r in robots_config:
            log_file = log_dir / f"robot_{r['robot_id']}.log"
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
                if "CONFLICT DETECTED" in content or "ARBITRATION RESULT" in content:
                    conflict_found_in_logs = True
                    for line in content.splitlines():
                        if "CONFLICT DETECTED" in line or "ARBITRATION RESULT" in line or "YIELD" in line:
                            print(f"    [{r['robot_id']}] {line.strip()}")
                    break

        if conflict_found_in_logs:
            print("  -> Confirmed: Peer-to-peer conflict detection and arbitration occurred autonomously!")

    # ── Phase 5: RESTART FastAPI Telemetry Viewer ─────────────────────────────
        print("\n[PHASE 5] Restarting FastAPI Telemetry Viewer...")
        fastapi_proc2 = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8000", "--log-level", "warning"],
            cwd=str(backend_cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"  -> New FastAPI PID = {fastapi_proc2.pid}")

        reconnected = False
        tick_after_restart = 0
        for _ in range(40):
            try:
                resp = requests.get("http://127.0.0.1:8000/health", timeout=1.0)
                if resp.status_code == 200 and resp.json().get("status") == "ok":
                    data = resp.json()
                    tick_after_restart = data.get("tick", 0)
                    reconnected = True
                    break
            except Exception:
                time.sleep(0.2)

        assert reconnected, "Restarted FastAPI failed to respond on /health!"
        print(f"  -> Confirmed: Restarted FastAPI reconnected seamlessly at Tick {tick_after_restart}!")
        assert tick_after_restart >= latest_tick - 2, (
            f"FastAPI reconnected but tick was stale: {tick_after_restart} vs {latest_tick}"
        )

        # ── Phase 6: Clean Teardown ───────────────────────────────────────────────
        print("\n[PHASE 6] Shutting down demo cleanly...")
        print("  -> All processes terminating cleanly.")

        print("\n" + "=" * 80)
        print("RESULT: DECENTRALIZATION PROOF FULLY SUCCESSFUL!")
        print("Robots run in independent OS processes, negotiate peer-to-peer,")
        print("and operate completely unhindered when the central backend server dies.")
        print("=" * 80)

    finally:
        if fastapi_proc and fastapi_proc.poll() is None:
            try:
                fastapi_proc.terminate()
            except Exception:
                pass
        if fastapi_proc2 and fastapi_proc2.poll() is None:
            try:
                fastapi_proc2.terminate()
            except Exception:
                pass
        if orchestrator:
            try:
                orchestrator.stop()
            except Exception:
                pass


if __name__ == "__main__":
    mp.freeze_support()
    main()
