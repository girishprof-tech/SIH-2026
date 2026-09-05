"""
pathfinder.py — Space-Time A* pathfinding engine.
Owner: Member 2 — Core Algorithm Engineer.  SIH26123.

Implements the exact function contract locked in SCHEMA.md Section 8:

    def find_path(
        start: tuple[int, int],
        goal: tuple[int, int],
        current_tick: int,
        reservation_table: dict[tuple[int, int, int], str]
    ) -> list[dict]

Rules enforced (SCHEMA.md Section 1 & 8):
  * 4-directional movement only (NORTH/SOUTH/EAST/WEST), no diagonals.
  * A 90 deg or 180 deg heading change costs 1 tick, during which the robot
    is stationary but still occupies/reserves its current cell.
  * Two robots may never swap positions in a single tick (A: p1->p2 and
    B: p2->p1 at the same tick is forbidden) — checked explicitly.
  * The full computed path (start..goal) is treated as reserved by the
    caller (see reservations.py); this module only *respects* whatever is
    already in reservation_table, it does not mutate it.
  * Returns [] if no path exists (unreachable goal, or fully boxed in by
    reservations within the search horizon) rather than raising.

Design note — heading is not part of the locked function signature.
Turn cost genuinely depends on which way the robot is currently facing, so
the search state internally is (x, y, heading, t). To stay 100% compatible
with the contract for positional calls `find_path(start, goal, tick, table)`,
`start_heading` is an **optional** keyword-only extra: if the caller doesn't
know/care about heading, we infer a sensible starting heading (facing toward
the goal) so the first move is never penalised for a turn it didn't need.
Flag this assumption to Member 1/3 if exact heading-continuity matters for
scoring/animation — passing the robot's real last heading in is one extra
kwarg away.
"""

from __future__ import annotations

import heapq
from itertools import count
from typing import Dict, List, Optional, Tuple

from grid import WarehouseGrid, Position

ReservationTable = Dict[Tuple[int, int, int], str]

MOVE_COST_TICKS = 1
TURN_COST_TICKS = 1
WAIT_COST_TICKS = 1

HEADINGS: Tuple[str, ...] = ("NORTH", "SOUTH", "EAST", "WEST")

DELTA: Dict[str, Position] = {
    "NORTH": (0, -1),
    "SOUTH": (0, 1),
    "EAST": (1, 0),
    "WEST": (-1, 0),
}

# Search state = (x, y, heading, t)
State = Tuple[int, int, str, int]


class SpaceTimeAStarPlanner:
    """
    Bind once to a WarehouseGrid, then call plan_path() per robot/per tick.
    The grid's heuristic cache is shared across calls, so this is the object
    Member 4's simulation loop should hold onto rather than recreate.
    """

    def __init__(
        self,
        grid: WarehouseGrid,
        horizon_padding: int = 60,
        max_expansions: int = 200_000,
    ) -> None:
        self.grid = grid
        self.horizon_padding = horizon_padding
        self.max_expansions = max_expansions

    def plan_path(
        self,
        start: Position,
        goal: Position,
        current_tick: int,
        reservation_table: ReservationTable,
        robot_id: Optional[str] = None,
        start_heading: Optional[str] = None,
    ) -> List[dict]:
        start = (int(start[0]), int(start[1]))
        goal = (int(goal[0]), int(goal[1]))

        if not self.grid.in_bounds(start) or not self.grid.in_bounds(goal):
            return []
        if self.grid.is_obstacle(start) or self.grid.is_obstacle(goal):
            return []

        if start == goal:
            return [{"x": start[0], "y": start[1], "t": current_tick}]

        h0 = self.grid.true_distance(start, goal)
        if h0 is None:
            return []  # goal is unreachable on the static map, full stop

        if start_heading is None or start_heading not in HEADINGS:
            start_heading = self._heading_towards(start, goal)

        max_t = current_tick + h0 + self.horizon_padding
        start_state: State = (start[0], start[1], start_heading, current_tick)

        # Precompute resting locations for all other robots in reservation_table:
        # An agent that reaches its final reservation (x, y) at max_t remains stationary there for all t >= max_t
        resting_cells: Dict[Tuple[int, int], Tuple[str, int]] = {}
        robot_max_tick: Dict[str, Tuple[int, int, int]] = {}
        for (rx, ry, rt), rid in reservation_table.items():
            if rid not in robot_max_tick or rt > robot_max_tick[rid][2]:
                robot_max_tick[rid] = (rx, ry, rt)
        for rid, (rx, ry, max_t_val) in robot_max_tick.items():
            if rid != robot_id:
                resting_cells[(rx, ry)] = (rid, max_t_val)

        g_score: Dict[State, int] = {start_state: 0}
        came_from: Dict[State, State] = {}
        tie = count()
        open_heap: List[Tuple[int, int, State]] = [(h0, next(tie), start_state)]
        closed: set = set()
        expansions = 0

        while open_heap:
            _, _, state = heapq.heappop(open_heap)
            if state in closed:
                continue
            closed.add(state)

            x, y, _heading, t = state
            if (x, y) == goal:
                return self._reconstruct(came_from, state)

            expansions += 1
            if expansions > self.max_expansions:
                return []
            if t >= max_t:
                continue

            for nstate, cost in self._successors(state, reservation_table, robot_id, resting_cells):
                if nstate in closed:
                    continue
                tentative_g = g_score[state] + cost
                if tentative_g < g_score.get(nstate, float("inf")):
                    g_score[nstate] = tentative_g
                    came_from[nstate] = state
                    nx_, ny_, _nh, _nt = nstate
                    hval = self.grid.true_distance((nx_, ny_), goal)
                    if hval is None:
                        continue
                    heapq.heappush(open_heap, (tentative_g + hval, next(tie), nstate))

        return []  # search space exhausted within horizon — no path exists

    # -- successor generation --------------------------------------------

    def _successors(
        self,
        state: State,
        reservation_table: ReservationTable,
        robot_id: Optional[str],
        resting_cells: Optional[Dict[Tuple[int, int], Tuple[str, int]]] = None,
    ) -> List[Tuple[State, int]]:
        x, y, heading, t = state
        nt = t + 1
        out: List[Tuple[State, int]] = []

        # 1. Move forward one cell in the current heading.
        dx, dy = DELTA[heading]
        target = (x + dx, y + dy)
        if self.grid.is_free(target) and not self._vertex_blocked(
            target, nt, reservation_table, robot_id, resting_cells
        ):
            if not self._causes_swap((x, y), target, t, reservation_table, robot_id):
                out.append(((target[0], target[1], heading, nt), MOVE_COST_TICKS))

        # 2 & 3. Stay on the same cell for this tick, either turning to a new
        # heading or waiting (yielding). Both still occupy/reserve (x, y).
        if not self._vertex_blocked((x, y), nt, reservation_table, robot_id, resting_cells):
            for h2 in HEADINGS:
                cost = WAIT_COST_TICKS if h2 == heading else TURN_COST_TICKS
                out.append(((x, y, h2, nt), cost))

        return out

    # -- reservation-table checks ------------------------------------------

    @staticmethod
    def _vertex_blocked(
        pos: Position,
        t: int,
        reservation_table: ReservationTable,
        robot_id: Optional[str],
        resting_cells: Optional[Dict[Tuple[int, int], Tuple[str, int]]] = None,
    ) -> bool:
        occupant = reservation_table.get((pos[0], pos[1], t))
        if occupant is not None:
            return occupant != robot_id
        if resting_cells is not None:
            resting = resting_cells.get((pos[0], pos[1]))
            if resting is not None:
                other_id, rest_t = resting
                if other_id != robot_id and t >= rest_t:
                    return True
        return False

    @staticmethod
    def _causes_swap(
        cur: Position,
        nxt: Position,
        t: int,
        reservation_table: ReservationTable,
        robot_id: Optional[str],
    ) -> bool:
        """
        Forbid the case where some other robot R occupies `nxt` at time t and
        will be at `cur` at time t+1 — i.e. we would trade places with R in
        the same tick (SCHEMA.md Section 1, "Swap rule").
        """
        occupant_at_target_now = reservation_table.get((nxt[0], nxt[1], t))
        if occupant_at_target_now is None or occupant_at_target_now == robot_id:
            return False
        occupant_at_source_next = reservation_table.get((cur[0], cur[1], t + 1))
        return occupant_at_source_next == occupant_at_target_now

    # -- misc ---------------------------------------------------------------

    @staticmethod
    def _heading_towards(a: Position, b: Position) -> str:
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx) >= abs(dy):
            return "EAST" if dx >= 0 else "WEST"
        return "SOUTH" if dy >= 0 else "NORTH"

    @staticmethod
    def _reconstruct(came_from: Dict[State, State], end_state: State) -> List[dict]:
        states = [end_state]
        s = end_state
        while s in came_from:
            s = came_from[s]
            states.append(s)
        states.reverse()
        return [{"x": s[0], "y": s[1], "t": s[3]} for s in states]


# ---------------------------------------------------------------------------
# Module-level convenience API — this is the literal signature other members
# import and call per SCHEMA.md Section 8.
# ---------------------------------------------------------------------------

_default_planner: Optional[SpaceTimeAStarPlanner] = None


def configure_default_grid(
    obstacles: Optional[List[Position]] = None,
    width: int = 30,
    height: int = 30,
    **planner_kwargs,
) -> SpaceTimeAStarPlanner:
    """
    Call this ONCE at simulation startup (Member 4's FastAPI startup hook is
    the natural place) after loading Section 9's obstacle list, e.g.:

        configure_default_grid(obstacles=[(5,5), (5,6), (5,7)])

    Every later plain `find_path(...)` call will use this grid. Safe to call
    again if the map changes between demo runs.
    """
    global _default_planner
    grid = WarehouseGrid(obstacles=obstacles or [], width=width, height=height)
    _default_planner = SpaceTimeAStarPlanner(grid, **planner_kwargs)
    return _default_planner


def find_path(
    start: Tuple[int, int],
    goal: Tuple[int, int],
    current_tick: int,
    reservation_table: ReservationTable,
    robot_id: Optional[str] = None,
    start_heading: Optional[str] = None,
    grid: Optional[WarehouseGrid] = None,
) -> List[dict]:
    """
    Exact contract from SCHEMA.md Section 8. Positional call with 4 args
    works precisely as specified; `robot_id`, `start_heading`, and `grid`
    are optional additive kwargs (see module docstring for why `robot_id`
    and `start_heading` matter in practice).

    If `grid` is not passed, uses the grid set up via configure_default_grid()
    (or an empty 30x30 grid with no obstacles if that was never called).
    """
    global _default_planner
    if grid is not None:
        planner = SpaceTimeAStarPlanner(grid)
    else:
        if _default_planner is None:
            _default_planner = SpaceTimeAStarPlanner(WarehouseGrid())
        planner = _default_planner

    return planner.plan_path(
        start,
        goal,
        current_tick,
        reservation_table,
        robot_id=robot_id,
        start_heading=start_heading,
    )
