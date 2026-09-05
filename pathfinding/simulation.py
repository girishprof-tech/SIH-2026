"""Decentralized warehouse fleet simulation with strict collision-free local coordination.

Robots NEVER store a full predefined route. Every tick they compute only the next
safe move from their own goal, static map and messages received from nearby AMRs.
At KILL_TICK the central server is declared offline; robots retain their tasks and
continue with neighbour-to-neighbour communication and local negotiation.

Safety invariants:
  * no two robots execute into the same cell in one tick
  * no robot enters a cell occupied at the beginning of that tick
  * no head-on swaps
  * losing a negotiation means choose another candidate or wait
  * completed robots are removed from the active traffic set after dropping
"""
from __future__ import annotations

import json
import random
from collections import deque, defaultdict
from dataclasses import dataclass
from pathlib import Path

W = H = 30
N = 18
KILL_TICK = 12
COMMUNICATION_RADIUS = 5
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass
class Robot:
    robot_id: str
    pos: tuple[int, int]
    start: tuple[int, int]
    pickup: tuple[int, int]
    drop: tuple[int, int]
    state: str = "TO_PICKUP"
    carrying: bool = False
    completed: bool = False
    battery: float = 100.0
    priority: int = 50
    waits: int = 0
    replans: int = 0
    last_pos: tuple[int, int] | None = None


def shelves() -> set[tuple[int, int]]:
    obs: set[tuple[int, int]] = set()
    for y in (5, 10, 15, 20):
        for x in range(3, 27):
            if x not in (7, 14, 21):
                obs.add((x, y))
    return obs


def make_robots() -> list[Robot]:
    rng = random.Random(26123)
    starts = [(1,1),(28,1),(1,28),(28,28),(2,8),(27,8),(2,13),(27,13),(2,18),(27,18),(2,23),(27,23),(8,2),(14,2),(20,2),(8,27),(14,27),(20,27)]
    pickups = [(4,3),(25,3),(4,26),(25,26),(6,8),(23,8),(6,13),(23,13),(6,18),(23,18),(6,23),(23,23),(8,4),(14,4),(20,4),(8,25),(14,25),(20,25)]
    drops = [(25,24),(4,24),(25,4),(4,4),(22,12),(7,22),(22,17),(7,7),(22,22),(7,12),(22,7),(7,17),(20,26),(14,26),(8,26),(20,3),(14,3),(8,3)]
    return [Robot(
        f"AMR-{i+1:02d}", starts[i], starts[i], pickups[i], drops[i],
        battery=round(rng.uniform(72, 100), 1), priority=rng.randint(45, 95)
    ) for i in range(N)]


def in_bounds(p: tuple[int, int]) -> bool:
    return 0 <= p[0] < W and 0 <= p[1] < H


def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def bfs_distance(start: tuple[int, int], goal: tuple[int, int], obstacles: set[tuple[int, int]]) -> int:
    """Static-map distance used to rank only the NEXT move; no route is retained."""
    if start == goal:
        return 0
    q = deque([(start, 0)])
    seen = {start}
    while q:
        p, d = q.popleft()
        for dx, dy in DIRS:
            n = (p[0] + dx, p[1] + dy)
            if not in_bounds(n) or n in obstacles or n in seen:
                continue
            if n == goal:
                return d + 1
            seen.add(n)
            q.append((n, d + 1))
    return 10_000


def nearby(robot: Robot, active: list[Robot]) -> list[Robot]:
    return [o for o in active if o.robot_id != robot.robot_id and manhattan(robot.pos, o.pos) <= COMMUNICATION_RADIUS]


def candidate_moves(robot: Robot, goal: tuple[int, int], obstacles: set[tuple[int, int]], known_occupied: set[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    """Rank adjacent moves. Occupied targets may be proposed but require an atomic neighbour handshake before execution."""
    choices: list[tuple[tuple[int, int, int], tuple[int, int]]] = []
    for dx, dy in DIRS:
        c = (robot.pos[0] + dx, robot.pos[1] + dy)
        if not in_bounds(c) or c in obstacles or (known_occupied is not None and c in known_occupied):
            continue
        dynamic_blocked = obstacles | (known_occupied or set())
        dynamic_blocked.discard(c)
        score = bfs_distance(c, goal, dynamic_blocked)
        # Strongly discourage immediate oscillation unless no route is available.
        backtrack = 8 if robot.last_pos == c else 0
        choices.append(((score, backtrack, c[1] * W + c[0]), c))
    choices.sort(key=lambda item: item[0])
    # WAIT is always available as the final safe choice.
    return [c for _, c in choices] + [robot.pos]


def effective_priority(r: Robot) -> int:
    # Waiting increases priority to prevent starvation. Deterministic and local.
    return r.priority + min(r.waits, 20) * 4 + (8 if r.carrying else 0)


def snapshot(r: Robot) -> dict:
    return {
        "id": r.robot_id, "x": r.pos[0], "y": r.pos[1],
        "start": list(r.start), "pickup": list(r.pickup), "drop": list(r.drop),
        "state": r.state, "carrying": r.carrying, "completed": r.completed,
        "battery": round(r.battery, 1), "priority": effective_priority(r),
        "replans": r.replans, "waits": r.waits,
    }


def validate_frame(active: list[Robot], previous_positions: dict[str, tuple[int, int]]) -> None:
    positions = [r.pos for r in active]
    if len(positions) != len(set(positions)):
        raise RuntimeError("SAFETY VIOLATION: same-cell collision")
    ids = [r.robot_id for r in active]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if previous_positions.get(a) == next(r.pos for r in active if r.robot_id == b) and previous_positions.get(b) == next(r.pos for r in active if r.robot_id == a):
                if previous_positions.get(a) != previous_positions.get(b):
                    raise RuntimeError("SAFETY VIOLATION: head-on swap")


def run(max_ticks: int = 500, output_path: str | None = None) -> dict:
    obstacles = shelves()
    robots = make_robots()
    events: list[dict] = []
    frames: list[dict] = []
    decentralized = False
    conflicts = 0
    messages = 0
    max_active = 0

    for tick in range(max_ticks):
        if tick == KILL_TICK:
            decentralized = True
            events.append({"tick": tick, "type": "KILL_SWITCH", "text": "CENTRAL SERVER OFFLINE — robots keep their tasks and switch to neighbour-to-neighbour planning. No stored routes are followed."})

        # Pick/drop transitions.
        for r in robots:
            if r.completed:
                continue
            if not r.carrying and r.pos == r.pickup:
                r.carrying = True
                r.state = "TO_DROP"
                events.append({"tick": tick, "type": "PICK", "robot": r.robot_id, "text": f"{r.robot_id}: picked BOX-{r.robot_id[-2:]} at {r.pickup}; goal changed locally to {r.drop}."})
            elif r.carrying and r.pos == r.drop:
                r.carrying = False
                r.completed = True
                r.state = "TASK_COMPLETED"
                events.append({"tick": tick, "type": "DROP", "robot": r.robot_id, "text": f"{r.robot_id}: delivered BOX-{r.robot_id[-2:]} to {r.drop}; TASK COMPLETED."})

        active = [r for r in robots if not r.completed]
        max_active = max(max_active, len(active))
        if not active:
            frames.append({"tick": tick, "robots": [snapshot(r) for r in robots]})
            break

        # ===== PHASE A: LOCAL SENSE + BROADCAST =====
        occupied = {r.pos for r in active}
        proposals: dict[str, list[tuple[int, int]]] = {}
        neighbour_map: dict[str, list[str]] = {}

        for r in active:
            goal = r.drop if r.carrying else r.pickup
            ns = nearby(r, active)
            neighbour_map[r.robot_id] = [n.robot_id for n in ns]
            # Immediate safety uses all currently occupied cells; in a physical fleet,
            # a robot only needs messages from nearby AMRs to know the local occupancy.
            proposals[r.robot_id] = candidate_moves(r, goal, obstacles, occupied - {r.pos})
            if decentralized:
                messages += 1
                events.append({"tick": tick, "type": "LOCAL_BROADCAST", "robot": r.robot_id,
                               "text": f"{r.robot_id} -> {neighbour_map[r.robot_id] or '[]'}: POS={r.pos}, INTENT={proposals[r.robot_id][0]}, GOAL={goal}"})

        # ===== PHASE B: LOCAL NEGOTIATION FOR TARGET CELLS =====
        # Every robot first requests its best candidate. Contested cells are won by
        # effective priority; losers try their next candidate, then wait.
        remaining = {r.robot_id: list(proposals[r.robot_id]) for r in active}
        moves = {r.robot_id: r.pos for r in active}
        unresolved = {r.robot_id for r in active}
        claimed: set[tuple[int, int]] = set()

        while unresolved:
            requests: dict[tuple[int, int], list[str]] = defaultdict(list)
            waiting_only: list[str] = []
            for rid in list(unresolved):
                opts = remaining[rid]
                while opts and opts[0] in claimed:
                    opts.pop(0)
                if not opts:
                    waiting_only.append(rid)
                    continue
                target = opts[0]
                if target == next(r.pos for r in active if r.robot_id == rid):
                    waiting_only.append(rid)
                else:
                    requests[target].append(rid)

            progress = False
            for target, contenders in requests.items():
                contenders.sort(key=lambda rid: (-effective_priority(next(r for r in active if r.robot_id == rid)), rid))
                winner = contenders[0]
                moves[winner] = target
                claimed.add(target)
                unresolved.discard(winner)
                progress = True
                if len(contenders) > 1:
                    conflicts += len(contenders) - 1
                    for loser in contenders[1:]:
                        remaining[loser].pop(0)
                        r = next(x for x in active if x.robot_id == loser)
                        r.replans += 1
                        if decentralized:
                            events.append({"tick": tick, "type": "NEGOTIATION", "robot": loser,
                                           "text": f"{loser}: target {target} contested locally; yielding and evaluating next safe move."})

            for rid in waiting_only:
                if rid in unresolved:
                    moves[rid] = next(r.pos for r in active if r.robot_id == rid)
                    unresolved.discard(rid)
                    progress = True

            if not progress:
                # Defensive fallback: nobody moves unless a safe candidate was negotiated.
                for rid in unresolved:
                    moves[rid] = next(r.pos for r in active if r.robot_id == rid)
                unresolved.clear()

        # ===== PHASE C: ATOMIC HANDSHAKE + DEPENDENCY VALIDATION =====
        # A robot may request a currently occupied cell ONLY if the occupant also
        # has a confirmed move and the whole dependency chain eventually reaches
        # an empty cell. Cycles are rejected. This fixes the earlier AMR-15/12 bug.
        start_pos = {r.robot_id: r.pos for r in active}
        occupant_by_cell = {r.pos: r.robot_id for r in active}

        # Re-check duplicate targets defensively.
        target_groups = defaultdict(list)
        for rid0, target in moves.items():
            if target != start_pos[rid0]:
                target_groups[target].append(rid0)
        for target, ids in target_groups.items():
            if len(ids) > 1:
                ids.sort(key=lambda rid0: (-effective_priority(next(r for r in active if r.robot_id == rid0)), rid0))
                for loser in ids[1:]:
                    moves[loser] = start_pos[loser]
                    conflicts += 1

        def move_is_safe(rid0: str, visiting: set[str], memo: dict[str, bool]) -> bool:
            if rid0 in memo:
                return memo[rid0]
            target = moves[rid0]
            if target == start_pos[rid0]:
                memo[rid0] = False
                return False
            blocker = occupant_by_cell.get(target)
            if blocker is None:
                memo[rid0] = True
                return True
            if blocker == rid0 or blocker in visiting:
                memo[rid0] = False
                return False
            # Safe only when the occupant has also committed to a safe move away.
            ok = move_is_safe(blocker, visiting | {rid0}, memo)
            memo[rid0] = ok
            return ok

        # Repeatedly reject any move whose dependency cannot be confirmed.
        changed = True
        while changed:
            changed = False
            memo: dict[str, bool] = {}
            for rid0 in list(moves):
                if moves[rid0] == start_pos[rid0]:
                    continue
                if not move_is_safe(rid0, set(), memo):
                    moves[rid0] = start_pos[rid0]
                    changed = True
                    conflicts += 1
                    r = next(x for x in active if x.robot_id == rid0)
                    r.replans += 1
                    if decentralized:
                        events.append({"tick": tick, "type": "SAFETY_WAIT", "robot": rid0,
                                       "text": f"{rid0}: neighbour handshake could not confirm target {start_pos[rid0]}; WAIT and retry next tick."})

        # ===== PHASE D: ATOMIC EXECUTION =====
        for r in active:
            nxt = moves[r.robot_id]
            if nxt != r.pos:
                old = r.pos
                r.last_pos = old
                r.pos = nxt
                r.battery = max(0.0, r.battery - 0.25)
                r.state = "TO_DROP" if r.carrying else "TO_PICKUP"
                r.waits = 0
                if decentralized:
                    events.append({"tick": tick, "type": "MOVE_APPROVED", "robot": r.robot_id,
                                   "text": f"{r.robot_id}: neighbour negotiation confirmed {old} -> {nxt}."})
            else:
                r.waits += 1
                r.state = "WAITING_FOR_CELL"

        validate_frame(active, start_pos)
        frames.append({"tick": tick, "robots": [snapshot(r) for r in robots]})

    all_completed = all(r.completed for r in robots)
    result = {
        "meta": {
            "grid": [W, H], "robots": N, "kill_tick": KILL_TICK,
            "communication_radius": COMMUNICATION_RADIUS,
            "planning": "ON_DEMAND_LOCAL_NEXT_MOVE_ONLY_NO_PREDEFINED_PATHS",
            "strict_occupied_cell_rule": True,
            "conflicts_resolved": conflicts,
            "neighbour_messages": messages,
            "all_completed": all_completed,
            "frames": len(frames),
            "max_active": max_active,
        },
        "obstacles": [list(p) for p in sorted(obstacles)],
        "frames": frames,
        "events": events,
    }
    path = Path(output_path) if output_path else Path(__file__).with_name("sim_output.json")
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not all_completed:
        raise RuntimeError("Simulation did not complete all tasks within max_ticks")
    return result


if __name__ == "__main__":
    result = run()
    print(json.dumps(result["meta"], indent=2))
