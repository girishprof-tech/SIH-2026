"""
test_combined_fixes.py — Rigorous verification suite for Parts A, B, C, D fixes:
  Part A: Real gate routing & loud validation
  Part B: Bounded sortation section, G2P handoff, Carton buffers, backpressure
  Part C: Weight-aware anticipatory charging, reachable station filtering, charger claims
  Part D: Ledger pod-weight realism, weight-proportional inertia pauses
"""

import pytest
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend" / "app" / "services"))
sys.path.insert(0, str(ROOT_DIR / "backend" / "backend"))
sys.path.insert(0, str(ROOT_DIR / "archive" / "pathfinding"))
sys.path.insert(0, str(ROOT_DIR / "conflict-engine"))

from app.models.world import WorldConfig, build_default_world
from app.models.carton import Carton
from app.models.task import Task, TaskType, TaskStatus
from app.models.robot import AMRType
from app.services.inventory_ledger import InventoryLedger, DEFAULT_BOX_WEIGHT_KG
from app.services.reservations import (
    claim_charger,
    renew_charger_claim,
    release_charger,
    get_charger_claim,
    prune_stale_charger_claims,
    release_robot_charger_claims,
)
from app.services.robot_node import RobotNode
from app.transport.loopback_transport import LoopbackTransport, LoopbackNetworkHub
from app.models.robot_fsm import RobotState


# ============================================================================
# PART A TESTS: Real Gate Routing & Loud Validation
# ============================================================================

def test_part_a_gate_rotation():
    world = build_default_world()
    
    # Verify OUT-1 rotates across its cluster: (29, 8), (29, 9), (29, 10)
    pos1 = world.gate_position("OUT-1")
    pos2 = world.gate_position("OUT-1")
    pos3 = world.gate_position("OUT-1")
    pos4 = world.gate_position("OUT-1")
    
    cluster_out1 = [(29, 8), (29, 9), (29, 10)]
    assert pos1 in cluster_out1
    assert pos2 in cluster_out1
    assert pos3 in cluster_out1
    assert pos1 != pos2 or len(cluster_out1) == 1
    assert pos4 == pos1  # Rotated back to start


def test_part_a_missing_gate_id_raises_loudly():
    bad_chutes = {
        "CHUTE-01": {"x": 23, "y": 2, "destination_zone": "ZONE_NORTH"},  # Missing gate_id!
    }
    with pytest.raises(ValueError, match="missing a required 'gate_id'"):
        WorldConfig(
            width=30, height=30, cell_size_m=1.0,
            static_obstacles=frozenset(),
            charging_stations=frozenset(),
            pickup_stations=frozenset(),
            dropoff_stations=frozenset(),
            sortation_chutes=bad_chutes,
        )


def test_part_a_invalid_gate_id_raises_loudly():
    bad_chutes = {
        "CHUTE-01": {"x": 23, "y": 2, "destination_zone": "ZONE_NORTH", "gate_id": "OUT-999"},
    }
    with pytest.raises(ValueError, match="references invalid gate_id 'OUT-999'"):
        WorldConfig(
            width=30, height=30, cell_size_m=1.0,
            static_obstacles=frozenset(),
            charging_stations=frozenset(),
            pickup_stations=frozenset(),
            dropoff_stations=frozenset(),
            sortation_chutes=bad_chutes,
        )


# ============================================================================
# PART B TESTS: Bounded Sortation, Pick Stations, Carton Buffers & Backpressure
# ============================================================================

def test_part_b_bounded_sortation_layout():
    world = build_default_world()

    # Pick stations exist at x=20
    assert "PICK-01" in world.pick_stations
    assert world.pick_stations["PICK-01"]["x"] == 20
    assert world.pick_stations["PICK-01"]["y"] == 2

    # Sortation zone bounded in x in [22, 27], y in [2, 5]
    bounds = world.sortation_zone["bounds"]
    assert bounds["min_x"] == 22
    assert bounds["max_x"] == 27
    assert bounds["min_y"] == 2
    assert bounds["max_y"] == 5
    assert (21, 3) in world.sortation_zone["entrances"]

    # All 8 chutes are inside sortation zone
    for chute_id, c in world.sortation_chutes.items():
        assert bounds["min_x"] <= c["x"] <= bounds["max_x"]
        assert bounds["min_y"] <= c["y"] <= bounds["max_y"]
        assert c["gate_id"] in world.export_gates


def test_part_b_pick_station_buffer_and_backpressure():
    world = build_default_world()
    
    # Capacity is 4 per station
    c1 = Carton(sku="SKU-A", qty=2, destination_zone="ZONE_NORTH", source_shelf_id="POD-01", created_tick=1, weight_kg=5.0)
    c2 = Carton(sku="SKU-B", qty=1, destination_zone="ZONE_SOUTH", source_shelf_id="POD-02", created_tick=2, weight_kg=2.5)
    
    st_id = world.deposit_carton_to_pick_station(c1, preferred_station_id="PICK-01")
    assert st_id == "PICK-01"
    assert len(world.pick_stations["PICK-01"]["buffer"]) == 1
    
    # Fill PICK-01 to capacity
    for _ in range(3):
        world.deposit_carton_to_pick_station(c2, preferred_station_id="PICK-01")
    assert len(world.pick_stations["PICK-01"]["buffer"]) == 4
    
    # Next preferred deposit to PICK-01 falls back to other stations (PICK-02 or 03)
    st_overflow = world.deposit_carton_to_pick_station(c1, preferred_station_id="PICK-01")
    assert st_overflow in ("PICK-02", "PICK-03")
    
    # Fill all stations completely
    for _ in range(7):
        world.deposit_carton_to_pick_station(c1)
    
    # Buffer saturation -> None returned (backpressure trigger)
    saturated = world.deposit_carton_to_pick_station(c1)
    assert saturated is None
    
    # Popping carton frees space
    popped = world.take_carton_from_pick_station("PICK-01")
    assert popped is not None
    assert popped.sku == "SKU-A"
    assert len(world.pick_stations["PICK-01"]["buffer"]) == 3
    
    # Now deposit succeeds again
    res = world.deposit_carton_to_pick_station(c1, preferred_station_id="PICK-01")
    assert res == "PICK-01"


def test_part_b_g2p_backpressure_hold():
    """Verify RobotNode returns BUFFER_FULL_WAIT frame when pick station buffer is saturated."""
    world = build_default_world()
    # Saturated all buffers
    c = Carton(sku="S", qty=1, destination_zone="Z", source_shelf_id="P", created_tick=0)
    for _ in range(12):
        world.deposit_carton_to_pick_station(c)
    
    transport = LoopbackTransport("R-G2P-TEST")
    node = RobotNode(
        robot_id="R-G2P-TEST",
        start_pos=(20, 2),
        robot_type="GOODS_TO_PERSON",
        transport=transport,
        world=world,
    )
    node.fsm.state = RobotState.DROPPING
    node.robot.carrying_pod_id = "POD-01"
    node.task = Task(
        task_id="TASK-PICK-TEST",
        pickup_x=4, pickup_y=4,
        dropoff_x=20, dropoff_y=2,
        urgency=3,
        created_tick=0,
        task_type=TaskType.PICK_ITEM,
        sku_to_pick="SKU-A",
        quantity=1,
    )

    frame = node.step(tick=10)
    assert frame["action"] == "BUFFER_FULL_WAIT"
    # State remains in DROPPING, pod is still carried
    assert node.fsm.state == RobotState.DROPPING
    assert node.robot.carrying_pod_id == "POD-01"


# ============================================================================
# PART C TESTS: Weight-Aware Anticipatory Charging & Leased Claims
# ============================================================================

def test_part_c_anticipatory_charging_threshold():
    """Far robot triggers charging sooner than near robot."""
    single_charger = {(2, 2)}
    transport1 = LoopbackTransport("R-NEAR")
    node_near = RobotNode(
        robot_id="R-NEAR",
        start_pos=(3, 2),  # very close to charger at (2, 2): dist = 1 cell
        transport=transport1,
        charging_stations=single_charger,
    )
    # dist to (2, 2) = 1 cell
    # dynamic threshold = 1 * 0.25 * 1.5 + 10 = 10.375%

    transport2 = LoopbackTransport("R-FAR")
    node_far = RobotNode(
        robot_id="R-FAR",
        start_pos=(28, 20),  # far from charger at (2, 2): dist = 26 + 18 = 44 cells
        transport=transport2,
        charging_stations=single_charger,
    )
    # dynamic threshold = 44 * 0.25 * 1.5 + 10 = 26.5%

    # At 20% battery:
    # Far robot (threshold 26.5%) MUST trigger charging
    node_far.robot.battery_pct = 20.0
    frame_far = node_far.step(tick=1)
    assert node_far.charger_target is not None
    assert node_far.fsm.state == RobotState.EN_ROUTE_PICKUP

    # Near robot (threshold 10.375%) does NOT trigger charging at 20%
    node_near.robot.battery_pct = 20.0
    frame_near = node_near.step(tick=1)
    assert node_near.charger_target is None
    assert node_near.fsm.state == RobotState.IDLE


def test_part_c_charger_claims_and_contention():
    """Verify atomic claims on charging stations and lease expiration."""
    from app.services.reservations import SHARED_CHARGER_CLAIMS
    SHARED_CHARGER_CLAIMS.clear()
    station = (2, 2)
    # Robot 1 claims station at tick 0
    assert claim_charger(station, "R-1", current_tick=0, lease_ticks=10)
    assert get_charger_claim(station, current_tick=5) == "R-1"
    
    # Robot 2 tries to claim same station at tick 5 -> rejected
    assert not claim_charger(station, "R-2", current_tick=5, lease_ticks=10)
    
    # Prune at tick 8 -> not stale yet
    pruned = prune_stale_charger_claims(current_tick=8)
    assert station not in pruned
    
    # At tick 11 -> lease expired
    assert get_charger_claim(station, current_tick=11) is None
    pruned = prune_stale_charger_claims(current_tick=11)
    assert station in pruned
    
    # Robot 2 can now claim it
    assert claim_charger(station, "R-2", current_tick=12, lease_ticks=10)
    assert get_charger_claim(station, current_tick=12) == "R-2"
    
    # Release claim
    release_charger(station, "R-2")
    assert get_charger_claim(station, current_tick=12) is None


# ============================================================================
# PART D TESTS: Pod-Weight Realism & Proportional Inertia Pauses
# ============================================================================

def test_part_d_shelf_weight_lookup(tmp_path):
    db_file = tmp_path / "test_inventory.db"
    ledger = InventoryLedger(db_path=db_file)
    world = build_default_world()
    ledger.seed_default_inventory(world)

    # Seeded shelf with ~33 boxes of 2.5kg = ~82.5kg
    w = ledger.get_shelf_weight_kg("POD-A01")
    assert w > 50.0  # heavy pod!

    # Empty / non-existent shelf
    w_empty = ledger.get_shelf_weight_kg("POD-NONEXISTENT")
    assert w_empty == 0.0


def test_part_d_inertia_pause_proportional_to_weight():
    """Heavy loads pause every 2 steps, light loads pause every 4 steps, 0 weight pauses 0."""
    world = build_default_world()
    transport = LoopbackTransport("R-LOAD-TEST")
    node = RobotNode(
        robot_id="R-LOAD-TEST",
        start_pos=(10, 10),
        transport=transport,
        world=world,
    )
    node.enable_load_weight_pause = True
    node.fsm.state = RobotState.EN_ROUTE_DROPOFF
    
    # Case 1: Heavy payload (80kg >= 50kg) -> interval = 2
    node.task = Task(
        task_id="T-HEAVY",
        pickup_x=10, pickup_y=10,
        dropoff_x=10, dropoff_y=15,
        urgency=3,
        created_tick=0,
        payload_weight_kg=80.0,
    )
    node.load_move_steps = 1
    # Next move step will be 2 -> pause!
    # Simulate move attempt
    node.robot.path = [{"x": 10, "y": 10, "t": 1}, {"x": 10, "y": 11, "t": 2}]
    node.load_move_steps = 2
    frame = node.step(tick=2)
    assert frame["action"] == "LOAD_WEIGHT_PAUSE"
    
    # Case 2: Light carton payload (5kg < 20kg) -> interval = 4
    node.task.payload_weight_kg = 5.0
    node.load_move_steps = 2
    # At step 2, light load should NOT pause
    node.robot.path = [{"x": 10, "y": 10, "t": 3}, {"x": 10, "y": 11, "t": 4}]
    frame = node.step(tick=3)
    assert frame["action"] == "MOVED"
    
    # At step 4, light load pauses
    node.load_move_steps = 4
    node.robot.path = [{"x": 10, "y": 11, "t": 4}, {"x": 10, "y": 12, "t": 5}]
    frame = node.step(tick=4)
    assert frame["action"] == "LOAD_WEIGHT_PAUSE"


def test_continuous_motion_default():
    """By default (ENABLE_LOAD_WEIGHT_PAUSE=False), robots move continuously every tick with zero pause."""
    world = build_default_world()
    transport = LoopbackTransport("R-CONTINUOUS-TEST")
    node = RobotNode(
        robot_id="R-CONTINUOUS-TEST",
        start_pos=(10, 10),
        transport=transport,
        world=world,
    )
    node.fsm.state = RobotState.EN_ROUTE_DROPOFF
    node.task = Task(
        task_id="T-CONTINUOUS",
        pickup_x=10, pickup_y=10,
        dropoff_x=10, dropoff_y=15,
        urgency=3,
        created_tick=0,
        payload_weight_kg=80.0,
    )
    node.load_move_steps = 2
    node.robot.path = [{"x": 10, "y": 10, "t": 1}, {"x": 10, "y": 11, "t": 2}]
    frame = node.step(tick=2)
    assert frame["action"] == "MOVED"
    assert node.robot.position == (10, 11)
