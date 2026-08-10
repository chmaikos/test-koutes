from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.warehouses import ReceiptMode


class WarehouseGovernanceFields(BaseModel):
    receipt_mode: ReceiptMode = ReceiptMode.auto_complete
    require_erp_document: bool = False
    quarantine_imports: bool = False
    quarantine_manual_receipts: bool = False
    two_person_approval_threshold: int | None = Field(default=None, gt=0)


class WarehousePlanningFields(WarehouseGovernanceFields):
    lead_time_days: int = Field(default=0, ge=0, le=365)
    safety_stock_percent: int = Field(default=0, ge=0, le=500)
    history_30_weight: int = Field(default=70, ge=0, le=1000)
    history_90_weight: int = Field(default=30, ge=0, le=1000)
    forecast_adjustment: int | None = Field(default=None, ge=-5000, le=5000)

    @model_validator(mode="after")
    def validate_history_weights(self) -> WarehousePlanningFields:
        if self.history_30_weight + self.history_90_weight <= 0:
            raise ValueError("30-day and 90-day history weights cannot both be zero")
        return self


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    min_inventory: int
    max_capacity: int
    min_pages_per_day: int | None
    lead_time_days: int
    safety_stock_percent: int
    history_30_weight: int
    history_90_weight: int
    forecast_adjustment: int | None
    receipt_mode: ReceiptMode
    require_erp_document: bool
    quarantine_imports: bool
    quarantine_manual_receipts: bool
    two_person_approval_threshold: int | None
    is_active: bool
    archived_at: datetime | None
    archived_by_user_id: int | None


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    min_inventory: int | None = Field(default=None, ge=0)
    max_capacity: int | None = Field(default=None, ge=1)
    min_pages_per_day: int | None = Field(default=None, gt=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=365)
    safety_stock_percent: int | None = Field(default=None, ge=0, le=500)
    history_30_weight: int | None = Field(default=None, ge=0, le=1000)
    history_90_weight: int | None = Field(default=None, ge=0, le=1000)
    forecast_adjustment: int | None = Field(default=None, ge=-5000, le=5000)
    receipt_mode: ReceiptMode | None = None
    require_erp_document: bool | None = None
    quarantine_imports: bool | None = None
    quarantine_manual_receipts: bool | None = None
    two_person_approval_threshold: int | None = Field(default=None, gt=0)


class WarehouseCreate(WarehousePlanningFields):
    name: str = Field(min_length=1, max_length=80)
    min_inventory: int = Field(default=0, ge=0)
    max_capacity: int = Field(default=1000, ge=1)
    min_pages_per_day: int | None = Field(default=None, gt=0)


class WarehousePolicyEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    changed_by_user_id: int | None
    old_policy: dict[str, object]
    new_policy: dict[str, object]
    changed_at: datetime
