class ConflictManager:

    def __init__(self, warehouse):

        self.warehouse = warehouse
        self.active_conflicts = []

    def clear(self):

        self.active_conflicts = []

    def calculate_priority(
        self,
        robot,
        urgency=0
    ):

        wait_ticks = getattr(
            robot,
            "wait_ticks",
            0
        )

        battery_bonus = 0

        if robot.battery_pct < 20:
            battery_bonus = 500

        distance = 0

        if robot.path:

            last = robot.path[-1]

            distance = abs(
                robot.x - last["x"]
            ) + abs(
                robot.y - last["y"]
            )

        return (
            urgency * 100
            + battery_bonus
            + wait_ticks * 10
            - distance
        )

    def detect_conflicts(self):

        self.clear()

        robots = self.warehouse.robots

        for i in range(len(robots)):

            for j in range(i + 1, len(robots)):

                robot_a = robots[i]
                robot_b = robots[j]

                distance = (
                    abs(robot_a.x - robot_b.x)
                    +
                    abs(robot_a.y - robot_b.y)
                )

                if distance > 2:
                    continue

                conflict_cell = self.check_path_overlap(
                    robot_a,
                    robot_b
                )

                if conflict_cell is not None:

                    self.active_conflicts.append({
                        "robot_ids": [
                            robot_a.robot_id,
                            robot_b.robot_id
                        ],
                        "cell": {
                            "x": conflict_cell[0],
                            "y": conflict_cell[1]
                        },
                        "resolved_by": None
                    })

        return self.active_conflicts

    def check_path_overlap(
        self,
        robot_a,
        robot_b
    ):

        path_a = robot_a.path
        path_b = robot_b.path

        for point_a in path_a[:3]:

            for point_b in path_b[:3]:

                if (
                    point_a["x"] == point_b["x"]
                    and
                    point_a["y"] == point_b["y"]
                    and
                    point_a["t"] == point_b["t"]
                ):

                    return (
                        point_a["x"],
                        point_a["y"]
                    )

        return None