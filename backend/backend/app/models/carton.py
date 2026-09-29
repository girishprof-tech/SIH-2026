"""
carton.py — Domain model for sortation cartons in SIH26123.
Represents a picked item carton placed into pick-station buffers and
transferred by SORTING AMRs to put-wall chutes.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class Carton:
    sku: str
    qty: int
    destination_zone: str
    source_shelf_id: str
    created_tick: int
    weight_kg: float = 0.0
    order_id: Optional[str] = None
    destination_gate: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Carton:
        return cls(
            sku=str(data.get("sku", "")),
            qty=int(data.get("qty", 1)),
            destination_zone=str(data.get("destination_zone", "OVERFLOW")),
            source_shelf_id=str(data.get("source_shelf_id", "")),
            created_tick=int(data.get("created_tick", 0)),
            weight_kg=float(data.get("weight_kg", 0.0)),
            order_id=data.get("order_id"),
            destination_gate=data.get("destination_gate"),
        )
