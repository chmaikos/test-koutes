from __future__ import annotations

from pydantic import BaseModel

from app.models.boxes import BoxStatus
from app.schemas.employees import WarehouseProductivityOut


class WarehouseSummary(BaseModel):
    warehouse_id: int
    name: str
    min_inventory: int
    max_capacity: int
    # ``inventory`` stays for back-compat (older API consumers + nginx
    # dashboards that haven't been redeployed yet) and equals
    # ``available_boxes + unavailable_boxes`` -- i.e. everything that
    # still occupies the warehouse.
    inventory: int
    # New split that powers the dashboard's two main bars:
    # - available = received + processing (closed-with-stuff +
    #   open-with-stuff) compared against ``min_inventory``.
    # - unavailable = incomplete + ready_to_return (open-but-empty +
    #   closed-and-done) compared against ``max_capacity``.
    available_boxes: int
    unavailable_boxes: int
    quarantined_boxes: int
    # Subset of ``unavailable_boxes`` shown separately on the dashboard
    # as "packaged for return". Exposing both lets the UI distinguish
    # boxes that are physically packed (ready_to_return) from boxes
    # that are emptied but not yet closed (incomplete).
    ready_to_return_boxes: int
    received_today: int
    returned_today: int
    # Count of boxes whose status moved OUT of ``processing`` today
    # (into ``incomplete`` or ``ready_to_return``). Captures how many
    # open boxes were finished today -- "box completion".
    completed_today: int
    # Projected completions per full calendar day at today's pace:
    # ``completed_today * 24 / elapsed_hours`` where ``elapsed_hours``
    # is wall-clock time since local midnight in APP_TIMEZONE (floored
    # at 1.0 so the first minute of the day never divides by zero).
    completed_per_day: float
    counts_by_status: dict[BoxStatus, int]
    open_alerts: int
    productivity_today: WarehouseProductivityOut | None = None
    productivity_week: WarehouseProductivityOut | None = None


class DashboardSummary(BaseModel):
    warehouses: list[WarehouseSummary]
    total_active_boxes: int
    total_available_boxes: int
    total_unavailable_boxes: int
    total_completed_today: int
    total_open_alerts: int
