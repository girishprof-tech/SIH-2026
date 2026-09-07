from collections import deque

from configuration.constants import (
    GRID_WIDTH,
    GRID_HEIGHT
)


class PathFinder:

    DIRECTIONS = [
        (0, -1, "NORTH"),
        (0, 1, "SOUTH"),
        (1, 0, "EAST"),
        (-1, 0, "WEST")
    ]

    def __init__(self, warehouse, reservation_table):

        self.warehouse = warehouse
        self.reservation_table = reservation_table

    def is_valid_cell(self, x, y):

        if x < 0 or x >= GRID_WIDTH:
            return False

        if y < 0 or y >= GRID_HEIGHT:
            return False

        if (x, y) in self.warehouse.obstacles:
            return False

        if (x, y) in self.warehouse.dynamic_obstacles:
            return False

        return True

    def find_path(
        self,
        start,
        goal,
        current_tick,
        robot_id
    ):

        queue = deque()

        queue.append(
            (
                start[0],
                start[1],
                current_tick
            )
        )

        parents = {}

        start_state = (
            start[0],
            start[1],
            current_tick
        )

        parents[start_state] = None

        while queue:

            x, y, tick = queue.popleft()

            if (x, y) == goal:

                return self.build_path(
                    parents,
                    (x, y, tick)
                )

            for dx, dy, heading in self.DIRECTIONS:

                nx = x + dx
                ny = y + dy
                nt = tick + 1

                if not self.is_valid_cell(nx, ny):
                    continue

                if self.reservation_table.is_reserved(
                    nx,
                    ny,
                    nt,
                    robot_id
                ):
                    continue

                state = (nx, ny, nt)

                if state in parents:
                    continue

                parents[state] = (
                    x,
                    y,
                    tick
                )

                queue.append(state)

        return []

    def build_path(self, parents, goal_state):

        path = []

        current = goal_state

        while current is not None:

            x, y, tick = current

            path.append({
                "x": x,
                "y": y,
                "t": tick
            })

            current = parents[current]

        path.reverse()

        return path