"""
backend/app/services/map_validator.py
======================================
Robust Map Validation Service for SIH26123 Warehouse Floor Layouts.

Validates:
1. Schema & format integrity (versioned schema, positive dimensions).
2. Boundary constraints (all entities strictly within [0..width-1, 0..height-1]).
3. Spatial overlap prevention (no blocked cells overlapping entities, no duplicate IDs, no stacked robots).
4. Fleet minimums & type requirements (G2P needs shelves & dropoffs, Sorting needs chutes).
5. Connectivity & Reachability (BFS):
   - Every robot start can reach a charger and relevant job targets.
   - Every shelf has at least one accessible aisle side.
   - Every station and gate is reachable from the open floor.
6. Deadlock hazard heuristics:
   - Detects 1-cell narrow corridors without passing bays and emits warnings.
"""

from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple


SUPPORTED_SCHEMA_VERSIONS = {"1.0.0", "1.0", "1"}


def validate_warehouse_map(data: Any) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    if not isinstance(data, dict):
        return {
            "valid": False,
            "errors": ["Map payload must be a JSON dictionary object."],
            "warnings": [],
            "stats": {},
        }

    # 1. Schema Version Check
    version = str(data.get("schema_version", "")).strip()
    if not version:
        errors.append("Missing required field: 'schema_version'.")
    elif not any(version.startswith(v) for v in ["1."]):
        errors.append(f"Unsupported schema_version '{version}'. Expected 1.x.x.")

    # 2. Grid Dimensions
    grid_info = data.get("grid")
    if not isinstance(grid_info, dict):
        errors.append("Missing or invalid 'grid' specification.")
        width, height = 0, 0
    else:
        try:
            width = int(grid_info.get("width", 0))
            height = int(grid_info.get("height", 0))
        except (ValueError, TypeError):
            width, height = 0, 0

        if width < 4 or height < 4:
            errors.append(f"Grid dimensions must be at least 4x4. Provided: {width}x{height}.")
        elif width > 200 or height > 200:
            errors.append(f"Grid dimensions exceed maximum allowable size (200x200). Provided: {width}x{height}.")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings, "stats": {}}

    def in_bounds(x: int, y: int) -> bool:
        return 0 <= x < width and 0 <= y < height

    # 3. Parse Entities
    blocked_cells: Set[Tuple[int, int]] = set()
    for idx, c in enumerate(data.get("blocked_cells", [])):
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            bx, by = int(c[0]), int(c[1])
        elif isinstance(c, dict):
            bx, by = int(c.get("x", -1)), int(c.get("y", -1))
        else:
            errors.append(f"Malformed blocked_cell at index {idx}.")
            continue
        if not in_bounds(bx, by):
            errors.append(f"Blocked cell ({bx}, {by}) is out of bounds [0..{width-1}, 0..{height-1}].")
        blocked_cells.add((bx, by))

    # Shelves
    shelves: Dict[str, Tuple[int, int]] = {}
    shelf_coords: Set[Tuple[int, int]] = set()
    for idx, s in enumerate(data.get("shelves", [])):
        sx, sy = int(s.get("x", -1)), int(s.get("y", -1))
        sid = s.get("id") or f"POD-R{sy:02d}C{sx:02d}"
        if sid in shelves:
            norm_id = f"POD-R{sy:02d}C{sx:02d}"
            if norm_id in shelves or norm_id == sid:
                norm_id = f"{sid}-DUP{idx+1}"
            warnings.append(f"Normalized duplicate shelf ID '{sid}' at ({sx}, {sy}) to '{norm_id}'.")
            sid = norm_id
            s["id"] = norm_id
        if not in_bounds(sx, sy):
            errors.append(f"Shelf '{sid}' at ({sx}, {sy}) is out of bounds.")
        if (sx, sy) in blocked_cells:
            errors.append(f"Shelf '{sid}' at ({sx}, {sy}) overlaps with a blocked cell.")
        if (sx, sy) in shelf_coords:
            errors.append(f"Multiple shelves placed on the same cell ({sx}, {sy}).")
        shelves[sid] = (sx, sy)
        shelf_coords.add((sx, sy))

    # Chargers
    chargers: Dict[str, Tuple[int, int]] = {}
    charger_coords: Set[Tuple[int, int]] = set()
    seen_charger_ids: Set[str] = set()
    for idx, chg in enumerate(data.get("chargers", [])):
        cx, cy = int(chg.get("x", -1)), int(chg.get("y", -1))
        cid = chg.get("id") or f"CHG-R{cy:02d}C{cx:02d}"
        if cid in seen_charger_ids:
            norm_cid = f"CHG-R{cy:02d}C{cx:02d}"
            if norm_cid in seen_charger_ids or norm_cid == cid:
                norm_cid = f"{cid}-DUP{idx+1}"
            warnings.append(f"Normalized duplicate charger ID '{cid}' at ({cx}, {cy}) to '{norm_cid}'.")
            cid = norm_cid
            chg["id"] = norm_cid
        seen_charger_ids.add(cid)
        if not in_bounds(cx, cy):
            errors.append(f"Charger '{cid}' at ({cx}, {cy}) is out of bounds.")
        if (cx, cy) in blocked_cells:
            errors.append(f"Charger '{cid}' at ({cx}, {cy}) overlaps with a blocked cell.")
        if (cx, cy) in shelf_coords:
            errors.append(f"Charger '{cid}' at ({cx}, {cy}) overlaps with a shelf.")
        chargers[cid] = (cx, cy)
        charger_coords.add((cx, cy))

    # Gates
    entry_gates = data.get("entry_gates", [])
    exit_gates = data.get("exit_gates", [])
    gate_coords: Set[Tuple[int, int]] = set()
    seen_gate_ids: Set[str] = set()
    for idx, g in enumerate(list(entry_gates) + list(exit_gates)):
        gid = g.get("id", f"GATE-{idx+1}")
        if gid in seen_gate_ids:
            norm_gid = f"{gid}-DUP{idx+1}"
            warnings.append(f"Normalized duplicate gate ID '{gid}' to '{norm_gid}'.")
            gid = norm_gid
            g["id"] = norm_gid
        seen_gate_ids.add(gid)
        cells = g.get("cells", [])
        if not cells and "x" in g and "y" in g:
            cells = [{"x": g["x"], "y": g["y"]}]
        for c in cells:
            gx, gy = int(c.get("x", -1)), int(c.get("y", -1))
            if not in_bounds(gx, gy):
                errors.append(f"Gate '{gid}' cell ({gx}, {gy}) is out of bounds.")
            if (gx, gy) in blocked_cells:
                errors.append(f"Gate '{gid}' cell ({gx}, {gy}) overlaps with a blocked cell.")
            if (gx, gy) in shelf_coords:
                errors.append(f"Gate '{gid}' cell ({gx}, {gy}) overlaps with a shelf.")
            gate_coords.add((gx, gy))

    # Sorting Stations / Chutes
    sorting_stations = data.get("sorting_stations", [])
    chute_coords: Set[Tuple[int, int]] = set()
    seen_chute_ids: Set[str] = set()
    for idx, s in enumerate(sorting_stations):
        sx, sy = int(s.get("x", -1)), int(s.get("y", -1))
        sid = s.get("id") or f"CHUTE-R{sy:02d}C{sx:02d}"
        if sid in seen_chute_ids:
            norm_sid = f"CHUTE-R{sy:02d}C{sx:02d}"
            if norm_sid in seen_chute_ids or norm_sid == sid:
                norm_sid = f"{sid}-DUP{idx+1}"
            warnings.append(f"Normalized duplicate sorting station ID '{sid}' at ({sx}, {sy}) to '{norm_sid}'.")
            sid = norm_sid
            s["id"] = norm_sid
        seen_chute_ids.add(sid)
        if not in_bounds(sx, sy):
            errors.append(f"Sorting station '{sid}' at ({sx}, {sy}) is out of bounds.")
        if (sx, sy) in blocked_cells:
            errors.append(f"Sorting station '{sid}' at ({sx}, {sy}) overlaps with a blocked cell.")
        if (sx, sy) in shelf_coords:
            errors.append(f"Sorting station '{sid}' at ({sx}, {sy}) overlaps with a shelf.")
        chute_coords.add((sx, sy))

        # 4c: Auto-assign chute "feeds exit gate" (default: nearest exit gate)
        if not s.get("gate_id") and not s.get("destination_zone"):
            nearest_gid = None
            min_dist = float("inf")
            for eg in exit_gates:
                eg_id = eg.get("id", "OUT-1")
                for c in eg.get("cells", []):
                    gx, gy = int(c.get("x", 0)), int(c.get("y", 0))
                    d = abs(sx - gx) + abs(sy - gy)
                    if d < min_dist:
                        min_dist = d
                        nearest_gid = eg_id
            if nearest_gid:
                s["gate_id"] = nearest_gid
                s["destination_zone"] = nearest_gid

    # Pick Stations
    pick_stations = data.get("pick_stations", [])
    pick_coords: Set[Tuple[int, int]] = set()
    seen_pick_ids: Set[str] = set()
    for idx, ps in enumerate(pick_stations):
        px, py = int(ps.get("x", -1)), int(ps.get("y", -1))
        pid = ps.get("id") or f"PICK-R{py:02d}C{px:02d}"
        if pid in seen_pick_ids:
            norm_pid = f"PICK-R{py:02d}C{px:02d}"
            if norm_pid in seen_pick_ids or norm_pid == pid:
                norm_pid = f"{pid}-DUP{idx+1}"
            warnings.append(f"Normalized duplicate pick station ID '{pid}' at ({px}, {py}) to '{norm_pid}'.")
            pid = norm_pid
            ps["id"] = norm_pid
        seen_pick_ids.add(pid)
        if not in_bounds(px, py):
            errors.append(f"Pick station '{pid}' at ({px}, {py}) is out of bounds.")
        if (px, py) in blocked_cells:
            errors.append(f"Pick station '{pid}' at ({px}, {py}) overlaps with a blocked cell.")
        if (px, py) in shelf_coords:
            errors.append(f"Pick station '{pid}' at ({px}, {py}) overlaps with a shelf.")
        pick_coords.add((px, py))

    # Robot Starts
    robot_starts = data.get("robot_starts", [])
    robot_coords: Set[Tuple[int, int]] = set()
    robot_types: Set[str] = set()
    seen_robot_ids: Set[str] = set()
    for idx, r in enumerate(robot_starts):
        rx, ry = int(r.get("x", -1)), int(r.get("y", -1))
        rid = r.get("id") or f"AMR-R{ry:02d}C{rx:02d}"
        if rid in seen_robot_ids:
            norm_rid = f"AMR-R{ry:02d}C{rx:02d}"
            if norm_rid in seen_robot_ids or norm_rid == rid:
                norm_rid = f"{rid}-DUP{idx+1}"
            warnings.append(f"Normalized duplicate robot ID '{rid}' to '{norm_rid}'.")
            rid = norm_rid
            r["id"] = norm_rid
        seen_robot_ids.add(rid)
        rtype = r.get("type", "GOODS_TO_PERSON")
        robot_types.add(rtype)
        if not in_bounds(rx, ry):
            errors.append(f"Robot '{rid}' start cell ({rx}, {ry}) is out of bounds [0..{width-1}, 0..{height-1}].")
        if (rx, ry) in blocked_cells:
            errors.append(f"Robot '{rid}' start position ({rx}, {ry}) overlaps with a blocked cell.")
        if (rx, ry) in shelf_coords:
            errors.append(f"Robot '{rid}' start position ({rx}, {ry}) cannot be inside a storage shelf.")
        if (rx, ry) in robot_coords:
            errors.append(f"Multiple robots spawned on the same start cell ({rx}, {ry}).")
        robot_coords.add((rx, ry))

    # 4. Fleet & Facility Minimums
    if len(robot_starts) < 1:
        errors.append("Map must configure at least one robot start position.")
    if len(entry_gates) + len(exit_gates) < 1:
        errors.append("Map must configure at least one gate (entry or exit dock).")
    if len(chargers) < 1:
        errors.append("Map must configure at least one charging station.")

    has_g2p = "GOODS_TO_PERSON" in robot_types
    has_sorting = "SORTING" in robot_types
    has_audit = "SCANNING_AUDIT" in robot_types

    if not has_g2p or not has_sorting:
        if has_audit and not has_g2p and not has_sorting:
            warnings.append("Only audit robots are configured. Warehouse will not accept customer orders until G2P and Sorting robots are added.")
        else:
            missing = []
            if not has_g2p:
                missing.append("Goods-to-Person (G2P)")
            if not has_sorting:
                missing.append("Sorting")
            warnings.append(f"Missing {' and '.join(missing)} robot(s). At least one G2P and one Sorting robot are required to accept orders.")

    # 5. Graph Connectivity & Reachability (BFS)
    # Walkway graph: all cells that are in-bounds, not blocked, not shelves
    walkway_cells = {
        (x, y)
        for x in range(width)
        for y in range(height)
        if (x, y) not in blocked_cells and (x, y) not in shelf_coords
    }

    def get_neighbors(cell: Tuple[int, int]) -> List[Tuple[int, int]]:
        x, y = cell
        res = []
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nx, ny = x + dx, y + dy
            if (nx, ny) in walkway_cells:
                res.append((nx, ny))
        return res

    # 5A. Accessible Aisle Side for Every Shelf
    for sid, (sx, sy) in shelves.items():
        has_aisle = False
        for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nx, ny = sx + dx, sy + dy
            if (nx, ny) in walkway_cells:
                has_aisle = True
                break
        if not has_aisle:
            errors.append(f"Shelf '{sid}' at ({sx}, {sy}) is completely enclosed with no accessible aisle side.")

    # 5B. Robot Reachability to Chargers & Targets
    for r in robot_starts:
        rid = r.get("id", "ROBOT")
        rx, ry = int(r.get("x", -1)), int(r.get("y", -1))
        if (rx, ry) not in walkway_cells:
            continue  # Already recorded error

        # BFS from robot start
        visited = set()
        queue = deque([(rx, ry)])
        visited.add((rx, ry))

        can_reach_charger = False
        reached_cells = set()

        while queue:
            curr = queue.popleft()
            reached_cells.add(curr)
            if curr in charger_coords:
                can_reach_charger = True

            for nxt in get_neighbors(curr):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

        if not can_reach_charger:
            errors.append(f"Robot '{rid}' at ({rx}, {ry}) cannot reach any charging station.")

        # Check reachability to at least one gate or target
        has_target = False
        for gc in gate_coords:
            if gc in reached_cells:
                has_target = True
                break
        for cc in chute_coords:
            if cc in reached_cells:
                has_target = True
                break
        for sid, spos in shelves.items():
            for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                if (spos[0] + dx, spos[1] + dy) in reached_cells:
                    has_target = True
                    break
            if has_target:
                break

        if not has_target:
            errors.append(f"Robot '{rid}' at ({rx}, {ry}) is isolated from all operational warehouse targets.")

        # Type-aware reachability checks (Step 4d)
        rtype = r.get("type", "GOODS_TO_PERSON")
        if rtype == "GOODS_TO_PERSON":
            can_reach_shelf = False
            for sid, spos in shelves.items():
                for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                    if (spos[0] + dx, spos[1] + dy) in reached_cells:
                        can_reach_shelf = True
                        break
                if can_reach_shelf:
                    break
            can_reach_pick = any(pc in reached_cells for pc in pick_coords)
            if not can_reach_shelf or not can_reach_pick:
                reasons = []
                if not can_reach_shelf:
                    reasons.append("storage shelf")
                if not can_reach_pick:
                    reasons.append("pick station")
                errors.append(f"G2P Robot '{rid}' cannot reach {' and '.join(reasons)}.")
        elif rtype == "SORTING":
            can_reach_pick = any(pc in reached_cells for pc in pick_coords)
            can_reach_chute = any(cc in reached_cells for cc in chute_coords)
            can_reach_exit = False
            for eg in exit_gates:
                cells = eg.get("cells", [])
                if not cells and "x" in eg and "y" in eg:
                    cells = [{"x": eg["x"], "y": eg["y"]}]
                for c in cells:
                    gx, gy = int(c.get("x", -1)), int(c.get("y", -1))
                    if (gx, gy) in reached_cells:
                        can_reach_exit = True
                        break
                if can_reach_exit:
                    break
            if not can_reach_pick or not can_reach_chute or not can_reach_exit:
                reasons = []
                if not can_reach_pick:
                    reasons.append("pick station")
                if not can_reach_chute:
                    reasons.append("chute")
                if not can_reach_exit:
                    reasons.append("exit gate")
                errors.append(f"Sorting Robot '{rid}' cannot reach {' and '.join(reasons)}.")

    # 5C. Every Gate & Station Reachable by Fleet
    # Union of all cells reachable by any robot start
    all_reachable_cells: Set[Tuple[int, int]] = set()
    for r in robot_starts:
        rx, ry = int(r.get("x", -1)), int(r.get("y", -1))
        if (rx, ry) in walkway_cells:
            visited = set()
            queue = deque([(rx, ry)])
            visited.add((rx, ry))
            while queue:
                curr = queue.popleft()
                all_reachable_cells.add(curr)
                for nxt in get_neighbors(curr):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)

    # Check each gate has at least one cell reachable
    for g in list(entry_gates) + list(exit_gates):
        gid = g.get("id", "GATE")
        cells = g.get("cells", [])
        if not cells and "x" in g and "y" in g:
            cells = [{"x": g["x"], "y": g["y"]}]
        gate_pts = [(int(c["x"]), int(c["y"])) for c in cells if "x" in c and "y" in c]
        if gate_pts and not any(pt in all_reachable_cells for pt in gate_pts):
            errors.append(f"Gate '{gid}' is isolated and unreachable from all robot start positions.")

    # Check each sorting station is reachable
    for s in sorting_stations:
        sid = s.get("id", "CHUTE")
        sx, sy = int(s.get("x", -1)), int(s.get("y", -1))
        if (sx, sy) not in all_reachable_cells:
            errors.append(f"Sorting station '{sid}' at ({sx}, {sy}) is isolated and unreachable from all robot start positions.")

    # Check each pick station is reachable
    for ps in pick_stations:
        pid = ps.get("id", "PICK")
        px, py = int(ps.get("x", -1)), int(ps.get("y", -1))
        if (px, py) not in all_reachable_cells:
            errors.append(f"Pick station '{pid}' at ({px}, {py}) is isolated and unreachable from all robot start positions.")

    # 6. Deadlock Warning Heuristics (1-cell wide narrow corridors without passing bays)
    for x in range(1, width - 1):
        for y in range(1, height - 1):
            if (x, y) in walkway_cells:
                north_free = (x, y - 1) in walkway_cells
                south_free = (x, y + 1) in walkway_cells
                east_free = (x + 1, y) in walkway_cells
                west_free = (x - 1, y) in walkway_cells

                # Vertical 1-cell corridor
                if north_free and south_free and not east_free and not west_free:
                    # Check if it extends >= 4 cells
                    span = 1
                    for dy in range(1, 5):
                        if (x, y + dy) in walkway_cells and not (x + 1, y + dy) in walkway_cells and not (x - 1, y + dy) in walkway_cells:
                            span += 1
                        else:
                            break
                    if span >= 4:
                        msg = f"Narrow 1-cell vertical aisle at column x={x}, y={y}..{y+span-1} risks bidirectional AMR deadlock."
                        if msg not in warnings:
                            warnings.append(msg)

                # Horizontal 1-cell corridor
                if east_free and west_free and not north_free and not south_free:
                    span = 1
                    for dx in range(1, 5):
                        if (x + dx, y) in walkway_cells and not (x + dx, y + 1) in walkway_cells and not (x + dx, y - 1) in walkway_cells:
                            span += 1
                        else:
                            break
                    if span >= 4:
                        msg = f"Narrow 1-cell horizontal aisle at row y={y}, x={x}..{x+span-1} risks bidirectional AMR deadlock."
                        if msg not in warnings:
                            warnings.append(msg)

    is_valid = (len(errors) == 0)
    stats = {
        "width": width,
        "height": height,
        "robots_count": len(robot_starts),
        "shelves_count": len(shelves),
        "chargers_count": len(chargers),
        "gates_count": len(gate_coords),
        "sorting_stations_count": len(sorting_stations),
        "pick_stations_count": len(pick_stations),
        "walkable_cells_count": len(walkway_cells),
    }

    return {
        "valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "stats": stats,
    }
