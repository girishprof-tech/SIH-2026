import pygame

from configuration.constants import (
    GRID_WIDTH,
    GRID_HEIGHT,
    CELL_SIZE
)


class Renderer:

    def __init__(self, screen):

        self.screen = screen

        self.font = pygame.font.SysFont(
            "Arial",
            12
        )

        self.small_font = pygame.font.SysFont(
            "Arial",
            10
        )

    def draw_warehouse(
        self,
        warehouse,
        tick=0,
        selected_robot=None,
        conflicts=None
    ):

        self.screen.fill(
            (240, 240, 240)
        )

        self.draw_grid()

        self.draw_obstacles(
            warehouse
        )

        self.draw_dynamic_obstacles(
            warehouse
        )

        self.draw_stations(
            warehouse
        )

        self.draw_paths(
            warehouse
        )

        self.draw_conflicts(
            conflicts or []
        )

        self.draw_robots(
            warehouse
        )

        self.draw_info(
            warehouse,
            tick,
            selected_robot
        )

    def draw_grid(self):

        for x in range(
            GRID_WIDTH + 1
        ):

            pixel_x = x * CELL_SIZE

            pygame.draw.line(
                self.screen,
                (200, 200, 200),
                (pixel_x, 0),
                (
                    pixel_x,
                    GRID_HEIGHT * CELL_SIZE
                )
            )

        for y in range(
            GRID_HEIGHT + 1
        ):

            pixel_y = y * CELL_SIZE

            pygame.draw.line(
                self.screen,
                (200, 200, 200),
                (0, pixel_y),
                (
                    GRID_WIDTH * CELL_SIZE,
                    pixel_y
                )
            )

    def draw_obstacles(
        self,
        warehouse
    ):

        for x, y in warehouse.obstacles:

            rect = pygame.Rect(
                x * CELL_SIZE,
                y * CELL_SIZE,
                CELL_SIZE,
                CELL_SIZE
            )

            pygame.draw.rect(
                self.screen,
                (80, 80, 80),
                rect
            )

    def draw_dynamic_obstacles(
        self,
        warehouse
    ):

        for x, y in warehouse.dynamic_obstacles:

            rect = pygame.Rect(
                x * CELL_SIZE,
                y * CELL_SIZE,
                CELL_SIZE,
                CELL_SIZE
            )

            pygame.draw.rect(
                self.screen,
                (200, 100, 100),
                rect
            )

    def draw_stations(
        self,
        warehouse
    ):

        for x, y in warehouse.pickup_stations:

            pygame.draw.rect(
                self.screen,
                (255, 200, 100),
                (
                    x * CELL_SIZE,
                    y * CELL_SIZE,
                    CELL_SIZE,
                    CELL_SIZE
                )
            )

        for x, y in warehouse.dropoff_stations:

            pygame.draw.rect(
                self.screen,
                (100, 200, 100),
                (
                    x * CELL_SIZE,
                    y * CELL_SIZE,
                    CELL_SIZE,
                    CELL_SIZE
                )
            )

        for x, y in warehouse.charging_stations:

            pygame.draw.rect(
                self.screen,
                (100, 150, 255),
                (
                    x * CELL_SIZE,
                    y * CELL_SIZE,
                    CELL_SIZE,
                    CELL_SIZE
                )
            )

    def draw_paths(
        self,
        warehouse
    ):

        for robot in warehouse.robots:

            for point in robot.path:

                x = (
                    point["x"]
                    * CELL_SIZE
                    + CELL_SIZE // 2
                )

                y = (
                    point["y"]
                    * CELL_SIZE
                    + CELL_SIZE // 2
                )

                pygame.draw.circle(
                    self.screen,
                    (180, 180, 220),
                    (x, y),
                    2
                )

    def draw_robots(
        self,
        warehouse
    ):

        for robot in warehouse.robots:

            center_x = (
                robot.x * CELL_SIZE
                + CELL_SIZE // 2
            )

            center_y = (
                robot.y * CELL_SIZE
                + CELL_SIZE // 2
            )

            pygame.draw.circle(
                self.screen,
                (50, 100, 200),
                (
                    center_x,
                    center_y
                ),
                CELL_SIZE // 3
            )

            self.draw_robot_heading(
                robot,
                center_x,
                center_y
            )

            text = self.small_font.render(
                robot.robot_id,
                True,
                (0, 0, 0)
            )

            self.screen.blit(
                text,
                (
                    center_x - 18,
                    center_y - 25
                )
            )

    def draw_robot_heading(
        self,
        robot,
        center_x,
        center_y
    ):

        if robot.heading == "NORTH":

            end_x = center_x
            end_y = (
                center_y
                - CELL_SIZE // 3
            )

        elif robot.heading == "SOUTH":

            end_x = center_x
            end_y = (
                center_y
                + CELL_SIZE // 3
            )

        elif robot.heading == "EAST":

            end_x = (
                center_x
                + CELL_SIZE // 3
            )

            end_y = center_y

        else:

            end_x = (
                center_x
                - CELL_SIZE // 3
            )

            end_y = center_y

        pygame.draw.line(
            self.screen,
            (255, 255, 255),
            (
                center_x,
                center_y
            ),
            (
                end_x,
                end_y
            ),
            3
        )

    def draw_conflicts(
        self,
        conflicts
    ):

        for conflict in conflicts:

            cell = conflict["cell"]

            rect = pygame.Rect(
                cell["x"] * CELL_SIZE,
                cell["y"] * CELL_SIZE,
                CELL_SIZE,
                CELL_SIZE
            )

            pygame.draw.rect(
                self.screen,
                (255, 0, 0),
                rect,
                3
            )

    def draw_info(
        self,
        warehouse,
        tick,
        selected_robot
    ):

        x = 730
        y = 20

        tick_text = self.font.render(
            f"Tick: {tick}",
            True,
            (0, 0, 0)
        )

        self.screen.blit(
            tick_text,
            (x, y)
        )

        y += 30

        if selected_robot is not None:

            lines = [
                f"ID: {selected_robot.robot_id}",
                (
                    f"Pos: "
                    f"({selected_robot.x}, "
                    f"{selected_robot.y})"
                ),
                f"Heading: {selected_robot.heading}",
                f"State: {selected_robot.state}",
                (
                    f"Battery: "
                    f"{selected_robot.battery_pct:.1f}%"
                ),
                (
                    f"Task: "
                    f"{selected_robot.current_task_id}"
                )
            ]

            for line in lines:

                text = self.small_font.render(
                    line,
                    True,
                    (0, 0, 0)
                )

                self.screen.blit(
                    text,
                    (x, y)
                )

                y += 18