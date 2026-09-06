class DynamicObstacleManager:

    def __init__(self, warehouse):

        self.warehouse = warehouse

    def add_obstacle(
        self,
        obstacle_id,
        x,
        y,
        created_tick,
        expires_at_tick
    ):

        self.warehouse.dynamic_obstacles[
            (x, y)
        ] = {
            "id": obstacle_id,
            "x": x,
            "y": y,
            "created_tick": created_tick,
            "expires_at_tick": expires_at_tick
        }

    def remove_expired(self, current_tick):

        expired = []

        for position, obstacle in (
            self.warehouse.dynamic_obstacles.items()
        ):

            if (
                current_tick
                >= obstacle["expires_at_tick"]
            ):

                expired.append(position)

        for position in expired:

            del self.warehouse.dynamic_obstacles[
                position
            ]

    def clear(self):

        self.warehouse.dynamic_obstacles.clear()