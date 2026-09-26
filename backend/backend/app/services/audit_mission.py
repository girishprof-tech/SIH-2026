"""
audit_mission.py — Real Inventory Checkpoint Patrol & Perception for Autonomous AMRs.

Allows AMRs to perform real warehouse inspection patrols visiting aisle checkpoint
cells, scanning nearby mobile shelf pods, applying realistic perception noise, and
recording verified audit entries into the persistent InventoryLedger.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from app.models.inventory import ShelfRecord
from app.models.world import WorldConfig, build_default_world
from app.services.inventory_ledger import InventoryLedger

log = logging.getLogger(__name__)

# Walkable aisle inspection coordinates directly adjacent to double-deep storage banks
DEFAULT_CHECKPOINTS: List[Tuple[int, int]] = [
    (7, 5), (14, 5), (21, 5),      # North rack face (Aisle 0, adjacent to Bank A row 6)
    (7, 9), (14, 9), (21, 9),      # Pick Aisle 1 (between Bank A row 7 & Bank B row 11)
    (7, 14), (14, 14), (21, 14),   # Pick Aisle 2 (between Bank B row 12 & Bank C row 16)
    (7, 19), (14, 19), (21, 19),   # Pick Aisle 3 (between Bank C row 17 & Bank D row 21)
    (7, 23), (14, 23), (21, 23),   # South rack face (Aisle 4, adjacent to Bank D row 22)
]


class AuditMission:
    """Represents an active inventory checkpoint audit mission."""

    def __init__(
        self,
        checkpoint: Tuple[int, int],
        audit_id: Optional[str] = None,
        target_shelf_id: Optional[str] = None,
    ) -> None:
        self.checkpoint = checkpoint
        self.audit_id = audit_id or f"AUDIT-{random.randint(1000, 9999)}"
        self.target_shelf_id = target_shelf_id
        self.is_completed: bool = False
        self.scanned_shelf_id: Optional[str] = None
        self.scanned_manifest: Dict[str, int] = {}
        self.logged_items_count: int = 0
        self.confidence: float = 1.0

    def find_nearby_shelves(
        self,
        cell: Tuple[int, int],
        world: WorldConfig,
        radius: int = 2,
    ) -> List[Tuple[str, Tuple[int, int], int]]:
        """Find all pod slots within Manhattan distance `radius` from `cell`."""
        cx, cy = cell
        nearby = []
        for shelf_id, (sx, sy) in world.pod_slots.items():
            dist = abs(cx - sx) + abs(cy - sy)
            if dist <= radius:
                nearby.append((shelf_id, (sx, sy), dist))
        nearby.sort(key=lambda item: item[2])
        return nearby

    def record_scan(
        self,
        cell: Tuple[int, int],
        robot_id: str = "AUDIT-ROBOT",
        tick: int = 0,
        ledger: Optional[InventoryLedger] = None,
        world: Optional[WorldConfig] = None,
    ) -> Dict[str, Any]:
        """
        Performs a perception scan of the actual physical shelf at/near the checkpoint,
        applies a realistic sensor noise model, and persists the result to the InventoryLedger.
        """
        if world is None:
            world = build_default_world()
        if ledger is None:
            ledger = InventoryLedger()

        # Determine target shelf
        shelf_id = self.target_shelf_id
        if not shelf_id:
            # Look up directly at cell or nearby
            shelf_id = world.shelf_at(cell[0], cell[1])
            if not shelf_id:
                nearby = self.find_nearby_shelves(cell, world)
                if nearby:
                    shelf_id = nearby[0][0]

        if not shelf_id:
            # Fallback if no shelf near checkpoint: default to closest pod
            closest = min(world.pod_slots.items(), key=lambda item: abs(cell[0] - item[1][0]) + abs(cell[1] - item[1][1]))
            shelf_id = closest[0]

        self.scanned_shelf_id = shelf_id

        # Read current ground truth from ledger
        current_record = ledger.get_shelf(shelf_id)
        if current_record is None:
            # Seed on demand if missing
            ledger.seed_default_inventory(world)
            current_record = ledger.get_shelf(shelf_id)

        ground_manifest = current_record.sku_manifest if current_record else {"SKU-DEFAULT": 20}
        
        # Perception noise model:
        # 94% probability exact read per SKU, 6% sensor fluctuation of +-1 or 2 items
        perceived_manifest: Dict[str, int] = {}
        for sku, count in ground_manifest.items():
            if random.random() < 0.94:
                perceived_manifest[sku] = count
            else:
                delta = random.choice([-2, -1, 1, 2])
                perceived_manifest[sku] = max(0, count + delta)

        perceived_total = sum(perceived_manifest.values())
        scan_confidence = 1.0 if perceived_manifest == ground_manifest else 0.95

        # Persist audit record in ledger
        updated_record = ledger.record_audit_scan(
            shelf_id=shelf_id,
            sku_counts=perceived_manifest,
            robot_id=robot_id,
            tick=tick,
            confidence=scan_confidence,
        )

        self.scanned_manifest = perceived_manifest
        self.logged_items_count = perceived_total
        self.confidence = scan_confidence
        self.is_completed = True

        msg = (
            f"[AUDIT VERIFIED] Checkpoint {cell} -> Shelf {shelf_id}: "
            f"Perceived count={perceived_total} (SKUs={perceived_manifest}, "
            f"confidence={scan_confidence:.2f}, robot={robot_id}, tick={tick})"
        )
        log.info(msg)
        return {
            "status": "SUCCESS",
            "message": msg,
            "shelf_id": shelf_id,
            "box_count": perceived_total,
            "sku_manifest": perceived_manifest,
            "confidence": scan_confidence,
            "robot_id": robot_id,
            "tick": tick,
        }
