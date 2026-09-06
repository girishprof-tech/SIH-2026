class Robot:

    def __init__(
        self,
        robot_id,
        x,
        y,
        heading
    ):

        self.robot_id = robot_id

        self.x = x
        self.y = y

        self.heading = heading

        self.path = []
        self.path_index = 0

        self.turning = False
        self.turn_ticks_remaining = 0

        # --------------------------------
        # State
        # --------------------------------

        self.state = "IDLE"

        # --------------------------------
        # Battery
        # --------------------------------

        self.battery_pct = 100.0

        # --------------------------------
        # Task
        # --------------------------------

        self.current_task_id = None

        # --------------------------------
        # Coordination
        # --------------------------------

        self.priority_score = 0.0
        self.wait_ticks = 0

        # --------------------------------
        # Simulation
        # --------------------------------

        self.last_updated_tick = 0

    def get_position(self):

        return (self.x, self.y)

    def set_position(self, x, y):

        self.x = x
        self.y = y

    def set_heading(self, heading):

        self.heading = heading

    def set_path(self, path):

        self.path = path
        self.path_index = 0

    def clear_path(self):

        self.path = []
        self.path_index = 0

    def has_finished_path(self):

        if not self.path:
            return True

        return self.path_index >= len(self.path) - 1