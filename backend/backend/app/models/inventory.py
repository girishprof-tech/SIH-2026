"""
inventory.py — Data models for shelves, pods, and warehouse inventory items.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class ShelfRecord:
    """
    State of a single mobile shelf pod in the warehouse.
    """
    shelf_id: str
    x: int
    y: int
    capacity_boxes: int = 100
    current_box_count: int = 0
    sku_manifest: Dict[str, int] = field(default_factory=dict)
    last_audited_tick: int = 0
    last_audited_by: Optional[str] = None
    confidence: float = 1.0  # 0.0 to 1.0

    def compute_decayed_confidence(self, current_tick: int, decay_rate: float = 0.002) -> float:
        """
        Monotonically decays confidence over ticks elapsed since last audit.
        Decay formula: max(0.05, confidence * exp(-decay_rate * elapsed_ticks))
        """
        elapsed = max(0, current_tick - self.last_audited_tick)
        decayed = self.confidence * math.exp(-decay_rate * elapsed)
        return max(0.05, min(1.0, round(decayed, 4)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shelf_id": self.shelf_id,
            "x": self.x,
            "y": self.y,
            "capacity_boxes": self.capacity_boxes,
            "current_box_count": self.current_box_count,
            "sku_manifest": dict(self.sku_manifest),
            "last_audited_tick": self.last_audited_tick,
            "last_audited_by": self.last_audited_by,
            "confidence": round(self.confidence, 4),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ShelfRecord:
        return cls(
            shelf_id=data["shelf_id"],
            x=int(data["x"]),
            y=int(data["y"]),
            capacity_boxes=int(data.get("capacity_boxes", 100)),
            current_box_count=int(data.get("current_box_count", 0)),
            sku_manifest=dict(data.get("sku_manifest", {})),
            last_audited_tick=int(data.get("last_audited_tick", 0)),
            last_audited_by=data.get("last_audited_by"),
            confidence=float(data.get("confidence", 1.0)),
        )
