"""
demo.py — small runnable example showing the intended integration pattern
for Member 3 (conflict engine) and Member 4 (backend broker).

Run with:  python3 demo.py
"""

from grid import WarehouseGrid
from pathfinder import SpaceTimeAStarPlanner
from reservations import reserve_path, release_reservations, prune_past

# 1. Load the static map once at startup (SCHEMA.md Section 9 shape).
STATIC_MAP = {
    "obstacles": [{"x": 5, "y": 5}, {"x": 5, "y": 6}, {"x": 5, "y": 7}],
    "charging_stations": [{"x": 0, "y": 0}, {"x": 29, "y": 29}],
    "pickup_stations": [{"x": 4, "y": 22}],
}
grid = WarehouseGrid.from_schema_dict(STATIC_MAP)
planner = SpaceTimeAStarPlanner(grid)

# 2. Shared reservation table — this dict is what Member 3's conflict engine
#    and Member 4's simulation loop both read/write every tick.
reservation_table = {}

# 3. Plan AMR-01's route for a freshly-assigned task.
current_tick = 118
path_01 = planner.plan_path(
    start=(12, 7),
    goal=(27, 3),
    current_tick=current_tick,
    reservation_table=reservation_table,
    robot_id="AMR-01",
    start_heading="NORTH",
)
reserve_path(path_01, "AMR-01", reservation_table)
print(f"AMR-01 path ({len(path_01)} steps):")
for step in path_01[:5]:
    print(" ", step)
print("  ...")

# 4. A second robot plans around AMR-01's freshly-made reservation.
path_03 = planner.plan_path(
    start=(4, 22),
    goal=(0, 0),
    current_tick=current_tick,
    reservation_table=reservation_table,
    robot_id="AMR-03",
    start_heading="WEST",
)
reserve_path(path_03, "AMR-03", reservation_table)
print(f"\nAMR-03 path ({len(path_03)} steps), planned around AMR-01's reservation.")

# 5. AMR-01 gets re-tasked mid-route -> release its old claim before replanning.
release_reservations("AMR-01", reservation_table)
new_goal = (20, 20)
path_01_v2 = planner.plan_path(
    start=(15, 5),  # wherever AMR-01 currently is
    goal=new_goal,
    current_tick=current_tick + 10,
    reservation_table=reservation_table,
    robot_id="AMR-01",
)
reserve_path(path_01_v2, "AMR-01", reservation_table)
print(f"\nAMR-01 replanned to new goal {new_goal}: {len(path_01_v2)} steps.")

# 6. Housekeeping: call once per tick to keep the table from growing forever.
removed = prune_past(reservation_table, current_tick=current_tick + 10)
print(f"\nPruned {removed} stale reservation entries.")
