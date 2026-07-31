from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    full_name: str
    default_hours_per_day: Decimal
    is_active: bool
    excluded_from_metrics: bool
    created_at: datetime
    updated_at: datetime


class EmployeeCreate(BaseModel):
    warehouse_id: int = Field(ge=1)
    full_name: str = Field(min_length=1, max_length=160)
    default_hours_per_day: Decimal = Field(
        default=Decimal("8.00"), gt=0, le=24
    )
    excluded_from_metrics: bool = False


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    default_hours_per_day: Decimal | None = Field(default=None, gt=0, le=24)
    is_active: bool | None = None
    excluded_from_metrics: bool | None = None
    warehouse_id: int | None = Field(default=None, ge=1)


class EmployeePage(BaseModel):
    """Paginated `GET /employees` response.

    Matches the shape used by `GET /boxes` so the SPA can reuse its
    generic `Page<T>` type. Callers that need every employee (dropdowns,
    name lookups) pass a large ``page_size`` rather than a separate
    endpoint; ``500`` is the hard cap.
    """

    items: list[EmployeeOut]
    total: int
    page: int
    page_size: int


class EmployeeImportItem(BaseModel):
    source_row: int = Field(ge=1)
    full_name: str = Field(min_length=1, max_length=160)
    default_hours_per_day: Decimal | None = Field(default=None, gt=0, le=24)
    is_active: bool | None = None
    excluded_from_metrics: bool | None = None


class EmployeeImportRequest(BaseModel):
    warehouse_id: int = Field(ge=1)
    items: list[EmployeeImportItem] = Field(min_length=1, max_length=5000)


class EmployeeImportSkip(BaseModel):
    row: int
    full_name: str
    reason: str


class EmployeeImportResult(BaseModel):
    created: list[EmployeeOut]
    updated: list[EmployeeOut]
    skipped: list[EmployeeImportSkip]


class ProductivityEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    warehouse_id: int
    entry_date: date
    pages: int
    hours_worked: Decimal
    note: str | None
    excluded_from_metrics: bool
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime


class ProductivityEntryCreate(BaseModel):
    employee_id: int = Field(ge=1)
    entry_date: date
    pages: int = Field(gt=0)
    hours_worked: Decimal = Field(gt=0, le=24)
    note: str | None = Field(default=None, max_length=500)
    excluded_from_metrics: bool = False


class PerformerOut(BaseModel):
    employee_id: int
    employee_name: str
    pages: int
    hours: float
    pages_per_day: float


class WarehouseProductivityOut(BaseModel):
    warehouse_id: int
    total_pages: int
    total_hours: float
    avg_pages_per_day: float
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
    avg_pages_per_day: float


class EmployeePeriodAverageOut(BaseModel):
    period_start: date
    period_end: date
    total_pages: int
    total_hours: float
    entry_count: int
    pages_per_day: float | None
    below_minimum: bool | None


class EmployeeAverageOut(BaseModel):
    employee_id: int
    employee_name: str
    excluded_from_metrics: bool
    weekly: EmployeePeriodAverageOut
    monthly: EmployeePeriodAverageOut
    three_month: EmployeePeriodAverageOut
    consistently_below_minimum: bool


class EmployeeAveragesOut(BaseModel):
    warehouse_id: int
    anchor_date: date
    min_pages_per_day: int | None
    employees: list[EmployeeAverageOut]
