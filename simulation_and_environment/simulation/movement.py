from configuration.constants import (
    GRID_WIDTH,
    GRID_HEIGHT
)


class MovementController:

    def __init__(
        self,
        warehouse,
        pathfinder
    ):

        self.warehouse = warehouse
        self.pathfinder = pathfinder

    def is_inside_grid(self, x, y):

        return (
            0 <= x < GRID_WIDTH
            and
            0 <= y < GRID_HEIGHT
        )

    def is_obstacle(self, x, y):

        return (
            (x, y) in self.warehouse.obstacles
            or
            (x, y)
            in self.warehouse.dynamic_obstacles
        )

    def get_robot_at(
        self,
        x,
        y,
        ignore_robot=None
    ):

        for robot in self.warehouse.robots:

            if robot is ignore_robot:
                continue

            if (
                robot.x == x
                and
                robot.y == y
            ):

                return robot

        return None

    def can_move_to(
        self,
        robot,
        x,
        y
    ):

        if not self.is_inside_grid(x, y):
            return False

        if self.is_obstacle(x, y):
            return False

        other = self.get_robot_at(
            x,
            y,
            robot
        )

        if other is not None:
            return False

        return True

    def turn_robot(
        self,
        robot,
        new_heading
    ):

        robot.heading = new_heading

        robot.turning = True

        robot.turn_ticks_remaining = 1

    def process_turn(self, robot):

        if not robot.turning:
            return

        robot.turn_ticks_remaining -= 1

        if robot.turn_ticks_remaining <= 0:

            robot.turning = False

    def update_robot(
        self,
        robot
    ):

        if robot.turning:

            self.process_turn(robot)

            return

        if robot.has_finished_path():

            robot.state = "IDLE"

            return

        next_point = robot.path[
            robot.path_index + 1
        ]

        next_x = next_point["x"]
        next_y = next_point["y"]

        dx = next_x - robot.x
        dy = next_y - robot.y

        if dx > 0:
            required_heading = "EAST"

        elif dx < 0:
            required_heading = "WEST"

        elif dy > 0:
            required_heading = "SOUTH"

        else:
            required_heading = "NORTH"

        if robot.heading != required_heading:

            self.turn_robot(
                robot,
                required_heading
            )

            robot.battery_pct -= 0.5

            return

        if not self.can_move_to(
            robot,
            next_x,
            next_y
        ):

            robot.wait_ticks += 1

            robot.battery_pct -= 0.1

            return

        robot.set_position(
            next_x,
            next_y
        )

        robot.path_index += 1

        robot.wait_ticks = 0

        robot.battery_pct -= 1.0