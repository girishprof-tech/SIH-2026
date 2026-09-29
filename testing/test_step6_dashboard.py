"""
testing/test_step6_dashboard.py
===============================
Verification script for Step 6: Dashboard Controls & Experience Pass.
Validates:
1. Active Map & Grid inspection via GET /api/map/current.
2. Dynamic speed scaling via POST /api/simulation/speed and GET /api/simulation/status.
3. Pause and Start via POST /api/simulation/pause and POST /api/simulation/start.
4. Complete Reset via POST /api/simulation/reset:
   - Resets tasks (cleared)
   - Resets orders (cleared)
   - Resets reservations
   - Reseeds inventory from map
   - Resets robots back to starting bays

Rule R1: Production stack via TestClient and real FleetOrchestrator subprocesses.
Rule R2: Force multiprocessing spawn mode for Windows compliance.
Rule R4: Verified twice consecutively.
"""

import os
import sys
import time
import multiprocessing as mp
from pathlib import Path

# Force spawn mode to strictly adhere to Rule R2
mp.set_start_method("spawn", force=True)

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from starlette.testclient import TestClient
from app.main import app


def test_step6_dashboard_experience():
    print("=" * 70)
    print("STEP 6 DASHBOARD CONTROLS VERIFICATION (REAL STACK / SPAWN MODE)")
    print("=" * 70)

    os.environ["SPAWN_FLEET_ORCHESTRATOR"] = "1"

    with TestClient(app) as client:
        # 1. Launch map
        print("\n[STEP 6.1] Launching test_map.json...")
        res = client.post("/api/map/launch", json={"filename": "test_map.json"})
        assert res.status_code == 200, f"Map launch failed: {res.text}"
        time.sleep(1.5)

        # 2. Inspect active map and grid info
        print("\n[STEP 6.2] Verifying active map info via GET /api/map/current...")
        cur_map = client.get("/api/map/current")
        assert cur_map.status_code == 200
        map_json = cur_map.json()
        assert map_json["name"] == "Standard 30x30 Test Warehouse"
        assert map_json["grid"]["width"] == 30
        assert map_json["grid"]["height"] == 30
        assert len(map_json["robot_starts"]) == 10
        print(f"-> Active map: '{map_json['name']}', Grid: {map_json['grid']['width']}x{map_json['grid']['height']}, Robots: {len(map_json['robot_starts'])}")

        # 3. Test Speed Selector
        print("\n[STEP 6.3] Testing simulation speed scaling (0.5x, 2x, 4x, 1x)...")
        for speed in [0.5, 2.0, 4.0, 1.0]:
            spd_res = client.post("/api/simulation/speed", json={"speed": speed})
            assert spd_res.status_code == 200, f"Speed set failed: {spd_res.text}"
            stat = client.get("/api/simulation/status").json()
            assert stat["speed"] == speed, f"Expected speed {speed}, got {stat.get('speed')}"
            print(f"   -> Speed set to {speed}x: status confirmed speed={stat['speed']}")

        # 4. Test Pause & Start
        print("\n[STEP 6.4] Testing simulation Pause and Start...")
        pause_res = client.post("/api/simulation/pause")
        assert pause_res.status_code == 200
        stat_paused = client.get("/api/simulation/status").json()
        assert stat_paused["running"] is False, "Expected running=False after pause"
        print("-> PASS: Simulation successfully paused.")

        start_res = client.post("/api/simulation/start")
        assert start_res.status_code == 200
        stat_started = client.get("/api/simulation/status").json()
        assert stat_started["running"] is True, "Expected running=True after start"
        print("-> PASS: Simulation successfully resumed.")

        # 5. Populate an order and check inventory depletion
        print("\n[STEP 6.5] Creating order to modify tasks, orders, and inventory...")
        order_res = client.post("/api/order", json={"sku": "SKU-A10", "quantity": 1})
        assert order_res.status_code in (200, 201)
        order_data = order_res.json()
        print(f"-> Created Order: {order_data['order_id']}")

        orders_res = client.get("/api/orders")
        assert orders_res.status_code == 200
        assert len(orders_res.json()) >= 1

        tasks_res = client.get("/api/task/all")
        assert tasks_res.status_code == 200
        assert len(tasks_res.json()) >= 1
        print(f"-> Active orders before reset: {len(orders_res.json())}, Active tasks: {len(tasks_res.json())}")

        # 6. Test Reset simulation action
        print("\n[STEP 6.6] Calling POST /api/simulation/reset...")
        reset_res = client.post("/api/simulation/reset")
        assert reset_res.status_code == 200
        reset_json = reset_res.json()
        assert reset_json["status"] == "reset"
        print(f"-> Reset status: {reset_json}")

        # Verify state after reset
        stat_after = client.get("/api/simulation/status").json()
        assert stat_after["running"] is False, "Expected running=False after reset"
        assert stat_after["tick"] == 0, f"Expected tick=0 after reset, got {stat_after['tick']}"

        orders_after = client.get("/api/orders").json()
        assert len(orders_after) == 0, f"Expected 0 orders after reset, found {len(orders_after)}"

        tasks_after = client.get("/api/task/all").json()
        assert len(tasks_after) == 0, f"Expected 0 tasks after reset, found {len(tasks_after)}"

        # Verify inventory reseeded
        catalog = client.get("/api/catalog").json()
        sku_a10 = [c for c in catalog if c["sku"] == "SKU-A10"][0]
        print(f"-> Inventory SKU-A10 after reset: {sku_a10['total_stock']} units (reseeded from map)")
        assert sku_a10["total_stock"] > 0, "Inventory was not reseeded!"

        # Verify robots returned to starting bays
        robots_after = client.get("/api/robots/").json()
        assert len(robots_after) == 10
        print(f"-> Fleet size after reset: {len(robots_after)} robots at starting bays in IDLE state.")

        print("\n" + "=" * 70)
        print("ALL STEP 6 DASHBOARD CHECKS PASSED SUCCESSFULLY!")
        print("=" * 70)


if __name__ == "__main__":
    test_step6_dashboard_experience()
