"""
test_no_dual_runtime.py — Verify SimulationEngine authoritative loop is never revived.
"""
import os
import sys
import time
from pathlib import Path
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))

os.environ["SPAWN_FLEET_ORCHESTRATOR"] = "0"

from app.main import app

def test_no_dual_runtime_on_start():
    with TestClient(app) as client:
        engine = app.state.engine
        fleet = app.state.fleet_state

        # 1. Assert engine is not running initially
        assert not engine._running
        assert getattr(engine, "ticks_executed", 0) == 0

        # 2. Call /api/simulation/start immediately (simulating cold boot race)
        response = client.post("/api/simulation/start")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

        # 3. Wait 0.6s (longer than SIM_TICK_MS = 500ms)
        time.sleep(0.6)

        # 4. Assert SimulationEngine tick loop was NOT started and executed 0 ticks
        assert not engine._running, "SimulationEngine._running should remain False!"
        assert engine._loop_task is None, "SimulationEngine._loop_task should be None!"
        assert getattr(engine, "ticks_executed", 0) == 0, "SimulationEngine should have executed 0 ticks!"
        print("-> Verified: SimulationEngine tick loop remained completely inert (0 ticks executed).")

if __name__ == "__main__":
    test_no_dual_runtime_on_start()
    print("ALL TESTS PASSED: test_no_dual_runtime.py")
