class ReservationTable:

    def __init__(self):
        # (x, y, tick) -> robot_id
        self.reservations = {}

    def clear(self):
        self.reservations.clear()

    def reserve_path(self, robot_id, path):

        for point in path:

            key = (
                point["x"],
                point["y"],
                point["t"]
            )

            self.reservations[key] = robot_id

    def is_reserved(self, x, y, tick, robot_id=None):

        key = (x, y, tick)

        owner = self.reservations.get(key)

        if owner is None:
            return False

        if robot_id is not None and owner == robot_id:
            return False

        return True

    def get_owner(self, x, y, tick):

        return self.reservations.get(
            (x, y, tick)
        )