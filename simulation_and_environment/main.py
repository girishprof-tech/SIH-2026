import pygame

from configuration.constants import (
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    TICK_MS
)

from simulation.warehouse import Warehouse
from simulation.reservations import ReservationTable
from simulation.pathfinding import PathFinder
from simulation.movement import MovementController
from simulation.conflicts import ConflictManager
from simulation.dynamic_obstacles import (
    DynamicObstacleManager
)
from simulation.task_manager import TaskManager

from rendering.renderer import Renderer


# ========================================
# Initialization
# ========================================

pygame.init()

screen = pygame.display.set_mode(
    (
        WINDOW_WIDTH,
        WINDOW_HEIGHT
    )
)

pygame.display.set_caption(
    "SIH26123 - AMR Fleet Simulation"
)


# ========================================
# Simulation Objects
# ========================================

warehouse = Warehouse()

reservations = ReservationTable()

pathfinder = PathFinder(
    warehouse,
    reservations
)

movement = MovementController(
    warehouse,
    pathfinder
)

conflicts = ConflictManager(
    warehouse
)

dynamic_obstacles = (
    DynamicObstacleManager(
        warehouse
    )
)

task_manager = TaskManager(
    warehouse
)

renderer = Renderer(
    screen
)


# ========================================
# Create Initial Task
# ========================================

task_manager.create_task(
    pickup=(2, 25),
    dropoff=(27, 4),
    urgency=3,
    current_tick=0
)


# ========================================
# Simulation Variables
# ========================================

clock = pygame.time.Clock()

running = True

paused = False

tick = 0

last_tick_time = pygame.time.get_ticks()

selected_robot = None


# ========================================
# Main Loop
# ========================================

while running:

    # ------------------------------------
    # Events
    # ------------------------------------

    for event in pygame.event.get():

        if event.type == pygame.QUIT:

            running = False

        # Space = Pause/Resume

        elif event.type == pygame.KEYDOWN:

            if event.key == pygame.K_SPACE:

                paused = not paused

            # R = Reset

            elif event.key == pygame.K_r:

                warehouse = Warehouse()

                reservations.clear()

                pathfinder = PathFinder(
                    warehouse,
                    reservations
                )

                movement = MovementController(
                    warehouse,
                    pathfinder
                )

                conflicts = ConflictManager(
                    warehouse
                )

                dynamic_obstacles = (
                    DynamicObstacleManager(
                        warehouse
                    )
                )

                task_manager = TaskManager(
                    warehouse
                )

                task_manager.create_task(
                    pickup=(2, 25),
                    dropoff=(27, 4),
                    urgency=3,
                    current_tick=0
                )

                tick = 0

                selected_robot = None

            # D = Add Temporary Obstacle

            elif event.key == pygame.K_d:

                # Example Temporary Obstacle

                dynamic_obstacles.add_obstacle(
                    obstacle_id=f"TEMP-{tick}",
                    x=20,
                    y=10,
                    created_tick=tick,
                    expires_at_tick=tick + 10
                )


    # ------------------------------------
    # Simulation Tick
    # ------------------------------------

    current_time = pygame.time.get_ticks()

    if (
        not paused
        and
        current_time - last_tick_time
        >= TICK_MS
    ):

        last_tick_time = current_time

        tick += 1

        # --------------------------------
        # Remove Expired Obstacles
        # --------------------------------

        dynamic_obstacles.remove_expired(
            tick
        )

        # --------------------------------
        # Assign Pending Tasks
        # --------------------------------

        task_manager.assign_tasks()

        # --------------------------------
        # Calculate Paths for Robots
        # --------------------------------

        reservations.clear()

        for robot in warehouse.robots:

            task = (
                task_manager.get_task_for_robot(
                    robot
                )
            )

            if task is None:
                continue

            if not robot.path:

                if task["status"] == "ASSIGNED":

                    goal = (
                        task["pickup"]["x"],
                        task["pickup"]["y"]
                    )

                    path = pathfinder.find_path(
                        robot.get_position(),
                        goal,
                        tick,
                        robot.robot_id
                    )

                    if path:

                        robot.set_path(
                            path
                        )

                        task["status"] = (
                            "IN_PROGRESS"
                        )

                        robot.state = (
                            "EN_ROUTE"
                        )

            reservations.reserve_path(
                robot.robot_id,
                robot.path
            )

        # --------------------------------
        # Detect Conflicts
        # --------------------------------

        active_conflicts = (
            conflicts.detect_conflicts()
        )

        # --------------------------------
        # Move Robots
        # --------------------------------

        for robot in warehouse.robots:

            movement.update_robot(
                robot
            )

            robot.last_updated_tick = tick


    # ------------------------------------
    # Render
    # ------------------------------------

    renderer.draw_warehouse(
        warehouse,
        tick=tick,
        selected_robot=selected_robot,
        conflicts=active_conflicts
        if 'active_conflicts' in locals()
        else []
    )

    pygame.display.flip()

    clock.tick(60)


pygame.quit()