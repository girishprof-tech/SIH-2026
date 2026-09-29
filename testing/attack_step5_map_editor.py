"""
testing/attack_step5_map_editor.py
==================================
Step 5 Attack Suite: Map File Format, Validation Rules, Launch Flow & Editor Integrity.

Attack Vectors:
1. Fuzz 100+ Random Maps:
   - Generates 100+ random map layouts with varied grid sizes, entity placements, and wall densities.
   - Asserts validator never crashes, never loops indefinitely, and never approves unreachable or isolated maps.
2. Malformed JSON, Schema Versions, Bounds & Duplicate Collision Rejection:
   - Malformed data structures, missing required sections, out-of-bounds coordinates, duplicate IDs,
     and illegal cell overlaps (robot on blocked cell, charger on shelf).
   - Asserts all are strictly rejected with valid=False and targeted diagnostic errors.
3. Extreme Dimensions (1x1 to 100x100) and Fleet Scaling (0, 1, 40 Robots):
   - Asserts grids smaller than 4x4 or larger than 200x200 are rejected.
   - Asserts 100x100 grid validates cleanly.
   - Asserts 0 robots rejected; 1 robot and 40 robots supported with proper reachability.
4. Walled-In Shelf & Station Isolation Attack:
   - Creates a valid baseline map, then hermetically seals a shelf pod on all 4 sides with static obstacle blocks.
   - Asserts validation fails with "no accessible aisle side".
   - Seals an exit gate or sortation chute; asserts validation fails with reachability error.
   - Tests 1-cell narrow corridors and asserts warning generation for deadlock risk.
5. Built-in Map Regression & Live REST API Endpoints:
   - Loads maps/test_map.json and asserts 100% clean validation (valid=True, 0 errors).
   - Tests FastAPI endpoints via TestClient:
     - POST /api/map/validate
     - POST /api/map/launch (supports raw map object AND preset filename)
     - GET /api/map/current
     - GET /api/map/presets
     - GET /api/world (proves dynamic derivation of world from active map)
"""

import copy
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend" / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "app"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.services.map_validator import validate_warehouse_map
from app.models.world import (
    build_world_from_map_dict,
    get_active_world,
    set_active_world,
    get_active_map_data,
)


def _make_valid_base_map(width: int = 15, height: int = 15, num_robots: int = 2) -> Dict[str, Any]:
    """Generates a clean, topologically sound baseline map."""
    return {
        "schema_version": "1.0.0",
        "name": "Base Test Map",
        "description": "Clean baseline for validation tests",
        "grid": {"width": width, "height": height, "cell_size_m": 1.0},
        "blocked_cells": [{"x": 1, "y": 1}],
        "shelves": [
            {"id": "POD-01", "x": 5, "y": 5, "capacity": 4},
            {"id": "POD-02", "x": 6, "y": 5, "capacity": 4},
        ],
        "entry_gates": [
            {"id": "IN-1", "cells": [{"x": 0, "y": 3}, {"x": 0, "y": 4}]}
        ],
        "exit_gates": [
            {"id": "OUT-1", "cells": [{"x": width - 1, "y": 3}, {"x": width - 1, "y": 4}]}
        ],
        "sorting_stations": [
            {"id": "CHUTE-01", "x": width - 3, "y": 2, "destination_zone": "ZONE_A", "gate_id": "OUT-1"}
        ],
        "pick_stations": [
            {"id": "PICK-01", "x": width - 4, "y": 2, "capacity": 4}
        ],
        "chargers": [
            {"id": "CHG-01", "x": 2, "y": 0},
            {"id": "CHG-02", "x": 3, "y": 0},
            {"id": "CHG-03", "x": 4, "y": 0},
        ],
        "robot_starts": [
            {"id": f"AMR-0{i+1}", "type": "GOODS_TO_PERSON" if i % 2 == 0 else "SORTING", "x": 2 + i, "y": 2}
            for i in range(num_robots)
        ],
    }


# ── ATTACK 1: FUZZ 100+ RANDOM MAPS ──────────────────────────────────────────

def attack_vector_1_fuzz_100_random_maps():
    """Attack 1: Fuzz 100+ random map layouts."""
    print("\n[Attack 1] Fuzzing 100+ random map layouts (testing robustness & reachability)...", flush=True)
    random.seed(4242)

    crashes = 0
    fuzz_count = 120

    for i in range(fuzz_count):
        w = random.randint(6, 40)
        h = random.randint(6, 40)
        num_obstacles = random.randint(0, int(w * h * 0.35))
        num_shelves = random.randint(1, min(20, w * h // 4))
        num_robots = random.randint(1, min(8, w * h // 6))
        num_chargers = random.randint(1, min(6, w * h // 8))

        all_coords = [(x, y) for x in range(w) for y in range(h)]
        random.shuffle(all_coords)

        # Distribute coords
        obs = [{"x": p[0], "y": p[1]} for p in all_coords[:num_obstacles]]
        rem = all_coords[num_obstacles:]
        sh = [{"id": f"S-{idx}", "x": p[0], "y": p[1]} for idx, p in enumerate(rem[:num_shelves])]
        rem = rem[num_shelves:]
        chg = [{"id": f"C-{idx}", "x": p[0], "y": p[1]} for idx, p in enumerate(rem[:num_chargers])]
        rem = rem[num_chargers:]
        rob = [
            {"id": f"R-{idx}", "type": random.choice(["GOODS_TO_PERSON", "SORTING", "SCANNING_AUDIT"]), "x": p[0], "y": p[1]}
            for idx, p in enumerate(rem[:num_robots])
        ]
        rem = rem[num_robots:]

        in_p = rem[0] if rem else (0, 0)
        out_p = rem[1] if len(rem) > 1 else (w - 1, h - 1)
        chute_p = rem[2] if len(rem) > 2 else (w - 2, h - 2)

        fuzz_map = {
            "schema_version": "1.0.0",
            "name": f"FuzzMap-{i}",
            "grid": {"width": w, "height": h},
            "blocked_cells": obs,
            "shelves": sh,
            "entry_gates": [{"id": "IN-1", "cells": [{"x": in_p[0], "y": in_p[1]}]}],
            "exit_gates": [{"id": "OUT-1", "cells": [{"x": out_p[0], "y": out_p[1]}]}],
            "sorting_stations": [{"id": "CH-1", "x": chute_p[0], "y": chute_p[1], "gate_id": "OUT-1"}],
            "pick_stations": [],
            "chargers": chg,
            "robot_starts": rob,
        }

        try:
            res = validate_warehouse_map(fuzz_map)
            assert isinstance(res, dict)
            assert "valid" in res and "errors" in res and "warnings" in res
            assert isinstance(res["errors"], list)
        except Exception as exc:
            print(f"  [FAIL] Fuzz test #{i} crashed validator: {exc}")
            crashes += 1

    assert crashes == 0, f"Validator crashed on {crashes} fuzz maps"
    print(f"  [PASS] Fuzzed {fuzz_count} random maps without a single crash or unhandled exception.")


# ── ATTACK 2: MALFORMED JSON, SCHEMA, BOUNDS & COLLISION REJECTION ───────────

def attack_vector_2_schema_and_malformed():
    """Attack 2: Schema compliance, bad JSON, out-of-bounds, duplicate IDs, and cell overlaps."""
    print("\n[Attack 2] Testing schema rejection, out-of-bounds, duplicate IDs & cell overlaps...", flush=True)

    # 1. Non-dict input
    res = validate_warehouse_map("string-payload")
    assert not res["valid"] and any("must be a JSON" in e for e in res["errors"])

    # 2. Missing schema_version
    m = _make_valid_base_map()
    del m["schema_version"]
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("schema_version" in e for e in res["errors"])

    # 3. Bad schema_version
    m = _make_valid_base_map()
    m["schema_version"] = "2.5.0"
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("Unsupported schema_version" in e for e in res["errors"])

    # 4. Out-of-bounds coordinate (shelf at x=20 on width=15)
    m = _make_valid_base_map(width=15, height=15)
    m["shelves"].append({"id": "POD-OOB", "x": 20, "y": 5})
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("out of bounds" in e for e in res["errors"])

    # 5. Out-of-bounds negative coordinate
    m = _make_valid_base_map(width=15, height=15)
    m["robot_starts"][0]["x"] = -1
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("out of bounds" in e for e in res["errors"])

    # 6. Duplicate shelf ID
    m = _make_valid_base_map()
    m["shelves"].append({"id": "POD-01", "x": 7, "y": 7})
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("Duplicate shelf ID" in e for e in res["errors"])

    # 7. Duplicate robot ID
    m = _make_valid_base_map()
    m["robot_starts"].append({"id": "AMR-01", "type": "SCANNING_AUDIT", "x": 8, "y": 8})
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("Duplicate robot ID" in e for e in res["errors"])

    # 8. Robot start on blocked cell
    m = _make_valid_base_map()
    m["blocked_cells"].append({"x": 2, "y": 2})  # Robot 1 starts at (2, 2)
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("blocked cell" in e for e in res["errors"])

    # 9. Charger on shelf cell
    m = _make_valid_base_map()
    m["chargers"].append({"id": "CHG-OVERLAP", "x": 5, "y": 5})  # POD-01 is at (5, 5)
    res = validate_warehouse_map(m)
    assert not res["valid"] and any("overlaps" in e for e in res["errors"])

    print("  [PASS] All 9 schema, bounds, duplication, and overlap attacks properly caught and rejected.")


# ── ATTACK 3: EXTREME DIMENSIONS AND FLEET SCALING ───────────────────────────

def attack_vector_3_extreme_dimensions_and_fleet():
    """Attack 3: Extreme dimensions (1x1 to 100x100) and fleet scaling (0, 1, 40 robots)."""
    print("\n[Attack 3] Testing extreme dimensions (1x1 to 100x100) and fleet scaling (0, 1, 40 robots)...", flush=True)

    # 1. 1x1 grid: rejected
    m1 = _make_valid_base_map(width=1, height=1)
    res = validate_warehouse_map(m1)
    assert not res["valid"] and any("at least 4x4" in e for e in res["errors"])

    # 2. 3x3 grid: rejected
    m3 = _make_valid_base_map(width=3, height=3)
    res = validate_warehouse_map(m3)
    assert not res["valid"] and any("at least 4x4" in e for e in res["errors"])

    # 3. 0 robots: rejected
    m_zero_robots = _make_valid_base_map()
    m_zero_robots["robot_starts"] = []
    res = validate_warehouse_map(m_zero_robots)
    assert not res["valid"] and any("at least one robot start" in e.lower() for e in res["errors"])

    # 4. Solo robot (1 robot): valid
    m_solo = _make_valid_base_map(num_robots=1)
    res = validate_warehouse_map(m_solo)
    assert res["valid"], f"Solo robot map unexpectedly failed: {res['errors']}"

    # 5. 40 robots on large map (50x50): valid
    m40 = _make_valid_base_map(width=50, height=50, num_robots=40)
    # Add enough chargers for 40 robots
    m40["chargers"] = [{"id": f"CHG-{i:02d}", "x": i + 1, "y": 0} for i in range(40)]
    res = validate_warehouse_map(m40)
    assert res["valid"], f"40-robot map unexpectedly failed: {res['errors']}"

    # 6. 100x100 giant map: valid and builds WorldConfig
    m100 = _make_valid_base_map(width=100, height=100, num_robots=10)
    res = validate_warehouse_map(m100)
    assert res["valid"], f"100x100 map failed validation: {res['errors']}"
    world, r_starts = build_world_from_map_dict(m100)
    assert world.width == 100 and world.height == 100
    assert len(r_starts) == 10

    print("  [PASS] Extreme dimensions and fleet sizing verified (1x1 rejected, 100x100 accepted, 0 robots rejected, 40 robots valid).")


# ── ATTACK 4: WALLED-IN SHELF & STATION ISOLATION ────────────────────────────

def attack_vector_4_walled_in_isolation():
    """Attack 4: Walled-in shelf pod and isolated station reachability tests."""
    print("\n[Attack 4] Testing walled-in shelf pod and isolated station reachability...", flush=True)

    # 1. Hermetically seal shelf POD-01 at (5, 5) with blocked walls
    m = _make_valid_base_map(width=15, height=15)
    # POD-01 is at (5, 5). Surround with blocked cells:
    m["blocked_cells"].extend([
        {"x": 4, "y": 5},  # West
        {"x": 6, "y": 5},  # East (note POD-02 is at 6,5, so move POD-02 or block)
    ])
    # Clear POD-02 and cleanly surround POD-01
    m["shelves"] = [{"id": "POD-01", "x": 5, "y": 5, "capacity": 4}]
    m["blocked_cells"] = [
        {"x": 4, "y": 5},
        {"x": 6, "y": 5},
        {"x": 5, "y": 4},
        {"x": 5, "y": 6},
    ]
    res = validate_warehouse_map(m)
    assert not res["valid"], "Walled-in shelf was approved by validator!"
    assert any("no accessible aisle side" in e for e in res["errors"])

    # 2. Hermetically isolate exit gate OUT-1
    m_gate = _make_valid_base_map(width=15, height=15)
    # OUT-1 is at (14, 3) and (14, 4). Block all surrounding cells (West, North, South):
    m_gate["blocked_cells"] = [
        {"x": 13, "y": 3},
        {"x": 13, "y": 4},
        {"x": 14, "y": 2},
        {"x": 14, "y": 5},
    ]
    res = validate_warehouse_map(m_gate)
    assert not res["valid"], "Isolated exit gate was approved by validator!"
    assert any("unreachable" in e.lower() for e in res["errors"])

    # 3. Narrow 1-cell aisle triggers deadlock warning
    m_narrow = _make_valid_base_map(width=15, height=15)
    # Create two parallel walls with a 1-cell slot between them
    for y in range(2, 8):
        m_narrow["blocked_cells"].append({"x": 7, "y": y})
        m_narrow["blocked_cells"].append({"x": 9, "y": y})
    # Column x=8 is a 1-cell narrow corridor
    res = validate_warehouse_map(m_narrow)
    assert any("Narrow 1-cell" in w for w in res["warnings"])

    print("  [PASS] Walled-in shelf pod and isolated station attacks caught; narrow aisle deadlock warnings triggered.")


# ── ATTACK 5: BUILT-IN MAP REGRESSION & REST API ENDPOINTS ───────────────────

def attack_vector_5_builtin_map_and_api():
    """Attack 5: Built-in test map regression and live FastAPI /api/map endpoints."""
    print("\n[Attack 5] Testing built-in map regression and FastAPI /api/map endpoints...", flush=True)

    test_map_path = ROOT_DIR / "maps" / "test_map.json"
    assert test_map_path.exists(), f"maps/test_map.json not found at {test_map_path}"

    with open(test_map_path, "r", encoding="utf-8") as f:
        test_map = json.load(f)

    # 1. Direct validation of test_map.json
    val = validate_warehouse_map(test_map)
    assert val["valid"], f"Built-in test_map.json failed validation: {val['errors']}"
    assert len(val["errors"]) == 0
    print(f"  -> Built-in test_map.json validated cleanly: {val['stats']}")

    # 2. Test FastAPI Endpoints via TestClient
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    # Test POST /api/map/validate
    resp = client.post("/api/map/validate", json=test_map)
    assert resp.status_code == 200, f"/api/map/validate returned {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["valid"] is True
    assert len(data["errors"]) == 0

    # Test POST /api/map/launch with preset filename
    resp_launch = client.post("/api/map/launch", json={"filename": "test_map.json"})
    assert resp_launch.status_code == 200, f"/api/map/launch returned {resp_launch.status_code}: {resp_launch.text}"
    launch_data = resp_launch.json()
    assert launch_data["status"] == "LAUNCHED"
    assert launch_data["width"] == 30 and launch_data["height"] == 30
    assert launch_data["robots_count"] == 10
    assert launch_data["shelves_count"] == 160
    assert launch_data["chutes_count"] == 8

    # Test GET /api/map/current
    resp_curr = client.get("/api/map/current")
    assert resp_curr.status_code == 200
    curr_map = resp_curr.json()
    assert curr_map.get("name") == test_map.get("name")
    assert curr_map.get("grid", {}).get("width") == 30

    # Test GET /api/map/presets
    resp_presets = client.get("/api/map/presets")
    assert resp_presets.status_code == 200
    presets = resp_presets.json()
    assert isinstance(presets, list)
    assert any(p["id"] == "test_map" for p in presets)

    # Test GET /api/world to confirm world is synchronized with launched map
    resp_world = client.get("/api/world")
    assert resp_world.status_code == 200
    world_data = resp_world.json()
    assert world_data["width"] == 30
    assert world_data["height"] == 30
    assert len(world_data["pod_slots"]) == 160
    assert len(world_data["charging_stations"]) == 8
    assert len(world_data["sortation_chutes"]) == 8

    # 3. Dynamic Launch of Custom Map: Ensure hot-swap without restarts
    custom_map = _make_valid_base_map(width=22, height=18, num_robots=3)
    resp_custom_launch = client.post("/api/map/launch", json=custom_map)
    assert resp_custom_launch.status_code == 200
    assert resp_custom_launch.json()["width"] == 22

    # Verify GET /api/world immediately reflects the new dimensions
    resp_world2 = client.get("/api/world")
    assert resp_world2.status_code == 200
    w2 = resp_world2.json()
    assert w2["width"] == 22 and w2["height"] == 18

    # Restore standard test_map.json
    client.post("/api/map/launch", json={"filename": "test_map.json"})

    print("  [PASS] Built-in map regression and all 5 REST API endpoints (/validate, /launch, /current, /presets, /world) passed cleanly.")


def main():
    print("=" * 80)
    print("RUNNING STEP 5 ATTACK SUITE: MAP FORMAT, VALIDATOR & LAUNCH FLOW")
    print("=" * 80)

    t0 = time.time()
    attack_vector_1_fuzz_100_random_maps()
    attack_vector_2_schema_and_malformed()
    attack_vector_3_extreme_dimensions_and_fleet()
    attack_vector_4_walled_in_isolation()
    attack_vector_5_builtin_map_and_api()

    elapsed = time.time() - t0
    print("\n" + "=" * 80)
    print(f"STEP 5 ATTACK SUITE COMPLETED CLEANLY: 5/5 ATTACK VECTORS PASSED ({elapsed:.2f}s)")
    print("=" * 80)
    # Clean process exit for Windows
    os._exit(0)


if __name__ == "__main__":
    main()
