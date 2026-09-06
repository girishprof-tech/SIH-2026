from simulation.robot import Robot


class Warehouse:

    def __init__(self):

        # Static Blocked Cells
        self.obstacles = set()

        # Special Warehouse Locations
        self.pickup_stations = set()
        self.dropoff_stations = set()
        self.charging_stations = set()

        self.dynamic_obstacles = {}
        
        # AMR Fleet
        self.robots = []

        self.create_layout()
        self.create_robots()

    def create_layout(self):

        # --------------------------------
        # Shelf / Rack 1
        # --------------------------------

        for y in range(3, 10):
            self.add_obstacle(5, y)

        # --------------------------------
        # Shelf / Rack 2
        # --------------------------------

        for y in range(3, 10):
            self.add_obstacle(10, y)

        # --------------------------------
        # Shelf / Rack 3
        # --------------------------------

        for y in range(3, 10):
            self.add_obstacle(15, y)

        # --------------------------------
        # Shelf / Rack 4
        # --------------------------------

        for y in range(15, 23):
            self.add_obstacle(8, y)

        # --------------------------------
        # Shelf / Rack 5
        # --------------------------------

        for y in range(15, 23):
            self.add_obstacle(13, y)

        # --------------------------------
        # Shelf / Rack 6
        # --------------------------------

        for y in range(15, 23):
            self.add_obstacle(18, y)

        # --------------------------------
        # Pickup Station
        # --------------------------------

        self.add_pickup_station(2, 25)

        # --------------------------------
        # Drop-Off Station
        # --------------------------------

        self.add_dropoff_station(27, 4)

        # --------------------------------
        # Charging Stations
        # --------------------------------

        self.add_charging_station(2, 2)
        self.add_charging_station(27, 27)

    def create_robots(self):

        initial_positions = [
            (2, 5, "EAST"),
            (2, 12, "EAST"),
            (2, 19, "EAST"),
            (2, 26, "EAST"),
            (7, 12, "NORTH"),
            (12, 12, "NORTH"),
            (17, 12, "NORTH"),
            (22, 12, "NORTH"),
            (22, 25, "WEST"),
            (27, 25, "WEST")
        ]

        for i, (x, y, heading) in enumerate(initial_positions):

            robot_id = f"AMR-{i + 1:02d}"

            robot = Robot(
                robot_id,
                x,
                y,
                heading
            )

            self.robots.append(robot)

        # test path for AMR-01
        self.robots[0].set_path([
            {"x": 2, "y": 5, "t": 0},
            {"x": 3, "y": 5, "t": 1},
            {"x": 4, "y": 5, "t": 2},
            {"x": 4, "y": 6, "t": 3},
            {"x": 4, "y": 7, "t": 4},
            {"x": 4, "y": 8, "t": 5},
            {"x": 4, "y": 9, "t": 6}
        ])

    def add_obstacle(self, x, y):
        self.obstacles.add((x, y))

    def add_pickup_station(self, x, y):
        self.pickup_stations.add((x, y))

    def add_dropoff_station(self, x, y):
        self.dropoff_stations.add((x, y))

    def add_charging_station(self, x, y):
        self.charging_stations.add((x, y))