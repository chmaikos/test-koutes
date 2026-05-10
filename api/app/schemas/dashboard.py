from __future__ import annotations

from pydantic import BaseModel

from app.models.boxes import BoxStatus
from app.schemas.employees import WarehouseProductivityOut


class WarehouseSummary(BaseModel):
    warehouse_id: int
    name: str
    min_inventory: int
    max_capacity: int
    inventory: int
    received_today: int
    returned_today: int
    counts_by_status: dict[BoxStatus, int]
    open_alerts: int
    productivity_today: WarehouseProductivityOut | None = None
    productivity_week: WarehouseProductivityOut | None = None


class DashboardSummary(BaseModel):
    warehouses: list[WarehouseSummary]
    total_active_boxes: int
    total_open_alerts: int
