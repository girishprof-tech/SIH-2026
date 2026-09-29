"""
backend/app/services/order_manager.py
======================================
Order Lifecycle Management & Stage Tracking for SIH26123.

Per-Order Stage Timeline:
  Announced -> G2P assigned -> Pod lifted -> At pick station -> Sorting assigned -> In chute -> Shipped
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class OrderStage(str, enum.Enum):
    ANNOUNCED = "Announced"
    G2P_ASSIGNED = "G2P assigned"
    POD_LIFTED = "Pod lifted"
    AT_PICK_STATION = "At pick station"
    SORTING_ASSIGNED = "Sorting assigned"
    IN_CHUTE = "In chute"
    SHIPPED = "Shipped"


STAGE_ORDER = [
    OrderStage.ANNOUNCED.value,
    OrderStage.G2P_ASSIGNED.value,
    OrderStage.POD_LIFTED.value,
    OrderStage.AT_PICK_STATION.value,
    OrderStage.SORTING_ASSIGNED.value,
    OrderStage.IN_CHUTE.value,
    OrderStage.SHIPPED.value,
]


@dataclass
class Order:
    order_id: str
    sku: str
    quantity: int
    destination_gate: str
    urgency: int
    created_tick: int
    stage: str = OrderStage.ANNOUNCED.value
    g2p_robot_id: Optional[str] = None
    sorting_robot_id: Optional[str] = None
    shelf_id: Optional[str] = None
    pick_station_id: Optional[str] = None
    chute_id: Optional[str] = None
    stuck_reason: Optional[str] = None
    stage_ticks: Dict[str, int] = field(default_factory=dict)
    task_ids: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def advance_stage(self, new_stage: str, current_tick: int) -> None:
        """Advance stage if it's further along in the lifecycle."""
        try:
            curr_idx = STAGE_ORDER.index(self.stage)
            new_idx = STAGE_ORDER.index(new_stage)
            if new_idx > curr_idx:
                self.stage = new_stage
                self.stage_ticks[new_stage] = current_tick
                self.stuck_reason = None
                self.updated_at = time.time()
                log.info(f"[OrderManager] Order {self.order_id} advanced to '{new_stage}' at tick {current_tick}")
        except ValueError:
            self.stage = new_stage
            self.stage_ticks[new_stage] = current_tick
            self.updated_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OrderManager:
    """Manages order creation, lifecycle state transitions, and stage tracking."""

    def __init__(self) -> None:
        self._orders: Dict[str, Order] = {}
        self._task_to_order: Dict[str, str] = {}
        self._shelf_to_order: Dict[str, str] = {}

    def add_order(self, order: Order) -> None:
        order.stage_ticks[OrderStage.ANNOUNCED.value] = order.created_tick
        self._orders[order.order_id] = order
        if order.shelf_id:
            self._shelf_to_order[order.shelf_id] = order.order_id
        for tid in order.task_ids:
            self._task_to_order[tid] = order.order_id

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def all_orders(self) -> List[Order]:
        return sorted(list(self._orders.values()), key=lambda o: o.created_tick, reverse=True)

    def link_task(self, task_id: str, order_id: str) -> None:
        self._task_to_order[task_id] = order_id
        order = self._orders.get(order_id)
        if order and task_id not in order.task_ids:
            order.task_ids.append(task_id)

    def get_order_by_task(self, task_id: str) -> Optional[Order]:
        oid = self._task_to_order.get(task_id)
        return self._orders.get(oid) if oid else None

    def get_order_by_shelf(self, shelf_id: str) -> Optional[Order]:
        oid = self._shelf_to_order.get(shelf_id)
        return self._orders.get(oid) if oid else None

    def on_task_claimed(self, task_id: str, robot_id: str, task_type_str: str, current_tick: int) -> None:
        order = self.get_order_by_task(task_id)
        if not order:
            return
        if "RETRIEVE_POD" in task_type_str or "STANDARD" in task_type_str:
            order.g2p_robot_id = robot_id
            order.advance_stage(OrderStage.G2P_ASSIGNED.value, current_tick)
        elif "TRANSFER" in task_type_str or "SORTATION" in task_type_str:
            order.sorting_robot_id = robot_id
            order.advance_stage(OrderStage.SORTING_ASSIGNED.value, current_tick)

    def on_pod_lifted(self, shelf_id: str, robot_id: str, current_tick: int) -> None:
        order = self.get_order_by_shelf(shelf_id)
        if order and order.stage in (OrderStage.ANNOUNCED.value, OrderStage.G2P_ASSIGNED.value):
            order.g2p_robot_id = robot_id
            order.advance_stage(OrderStage.POD_LIFTED.value, current_tick)

    def on_at_pick_station(self, shelf_id: str, pick_station_id: str, current_tick: int) -> None:
        order = self.get_order_by_shelf(shelf_id)
        if order:
            order.pick_station_id = pick_station_id
            order.advance_stage(OrderStage.AT_PICK_STATION.value, current_tick)

    def on_in_chute(self, order_id: str, chute_id: str, current_tick: int) -> None:
        order = self._orders.get(order_id)
        if order:
            order.chute_id = chute_id
            order.advance_stage(OrderStage.IN_CHUTE.value, current_tick)

    def on_shipped(self, order_id: str, current_tick: int) -> None:
        order = self._orders.get(order_id)
        if order:
            order.advance_stage(OrderStage.SHIPPED.value, current_tick)

    def update_stuck_reasons(self, current_tick: int) -> None:
        for order in self._orders.values():
            if order.stage == OrderStage.SHIPPED.value:
                order.stuck_reason = None
                continue

            last_stage_tick = order.stage_ticks.get(order.stage, order.created_tick)
            ticks_in_stage = current_tick - last_stage_tick

            if order.stage == OrderStage.ANNOUNCED.value and ticks_in_stage > 25:
                order.stuck_reason = "Waiting for an available Goods-to-Person AMR to claim auction"
            elif order.stage == OrderStage.G2P_ASSIGNED.value and ticks_in_stage > 40:
                order.stuck_reason = f"G2P robot {order.g2p_robot_id} traveling or navigating traffic"
            elif order.stage == OrderStage.AT_PICK_STATION.value and ticks_in_stage > 30:
                order.stuck_reason = "Waiting for an available Sorting AMR to transfer carton"
            elif order.stage == OrderStage.IN_CHUTE.value and ticks_in_stage > 40:
                order.stuck_reason = f"Carton in chute {order.chute_id}; awaiting dock dispatch"
            else:
                if ticks_in_stage <= 15:
                    order.stuck_reason = None

    def clear(self) -> None:
        self._orders.clear()
        self._task_to_order.clear()
        self._shelf_to_order.clear()
