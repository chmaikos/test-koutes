from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    full_name: str
    email: str | None
    default_hours_per_day: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EmployeeCreate(BaseModel):
    warehouse_id: int = Field(ge=1)
    full_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    default_hours_per_day: Decimal = Field(
        default=Decimal("8.00"), gt=0, le=24
    )


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    default_hours_per_day: Decimal | None = Field(default=None, gt=0, le=24)
    is_active: bool | None = None
    warehouse_id: int | None = Field(default=None, ge=1)


class ProductivityEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    warehouse_id: int
    entry_date: date
    pages: int
    hours_worked: Decimal
    note: str | None
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime


class ProductivityEntryCreate(BaseModel):
    employee_id: int = Field(ge=1)
    entry_date: date
    pages: int = Field(ge=0)
    hours_worked: Decimal = Field(gt=0, le=24)
    note: str | None = Field(default=None, max_length=500)


class PerformerOut(BaseModel):
    employee_id: int
    employee_name: str
    pages: int
    hours: float
    pages_per_hour: float


class WarehouseProductivityOut(BaseModel):
    warehouse_id: int
    total_pages: int
    total_hours: float
    avg_pages_per_hour: float
    entry_count: int
    active_employees: int
    top: list[PerformerOut]
    bottom: list[PerformerOut]


class ProductivitySummaryOut(BaseModel):
    """Wraps the per-warehouse summaries plus aggregated grand totals."""

    period_start: date
    period_end: date
    warehouses: list[WarehouseProductivityOut]
    total_pages: int
    total_hours: float
    avg_pages_per_hour: float
