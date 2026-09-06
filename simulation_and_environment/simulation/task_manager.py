class TaskManager:

    def __init__(self, warehouse):

        self.warehouse = warehouse

        self.tasks = []

        self.next_task_number = 1

    def create_task(
        self,
        pickup,
        dropoff,
        urgency,
        current_tick
    ):

        task_id = (
            f"TASK-{self.next_task_number:04d}"
        )

        self.next_task_number += 1

        task = {
            "task_id": task_id,
            "pickup": {
                "x": pickup[0],
                "y": pickup[1]
            },
            "dropoff": {
                "x": dropoff[0],
                "y": dropoff[1]
            },
            "urgency": urgency,
            "created_tick": current_tick,
            "assigned_robot_id": None,
            "status": "PENDING"
        }

        self.tasks.append(task)

        return task

    def assign_tasks(self):

        for task in self.tasks:

            if task["status"] != "PENDING":
                continue

            best_robot = None
            best_score = float("-inf")

            for robot in self.warehouse.robots:

                if robot.state != "IDLE":
                    continue

                distance = (
                    abs(
                        robot.x
                        -
                        task["pickup"]["x"]
                    )
                    +
                    abs(
                        robot.y
                        -
                        task["pickup"]["y"]
                    )
                )

                wait_ticks = robot.wait_ticks

                battery_bonus = 0

                if robot.battery_pct < 20:
                    battery_bonus = 500

                score = (
                    task["urgency"] * 100
                    +
                    battery_bonus
                    +
                    wait_ticks * 10
                    -
                    distance
                )

                if (
                    score > best_score
                    or
                    (
                        score == best_score
                        and
                        (
                            best_robot is None
                            or
                            robot.robot_id
                            <
                            best_robot.robot_id
                        )
                    )
                ):

                    best_score = score
                    best_robot = robot

            if best_robot is None:
                continue

            task["assigned_robot_id"] = (
                best_robot.robot_id
            )

            task["status"] = "ASSIGNED"

            best_robot.current_task_id = (
                task["task_id"]
            )

            best_robot.state = "EN_ROUTE"

            best_robot.priority_score = (
                best_score
            )

    def get_task_for_robot(self, robot):

        for task in self.tasks:

            if (
                task["assigned_robot_id"]
                ==
                robot.robot_id
            ):

                return task

        return None