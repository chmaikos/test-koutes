from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    min_inventory: int
    max_capacity: int
    min_pages_per_day: int | None
    is_active: bool
    archived_at: datetime | None
    archived_by_user_id: int | None


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    min_inventory: int | None = Field(default=None, ge=0)
    max_capacity: int | None = Field(default=None, ge=1)
    min_pages_per_day: int | None = Field(default=None, gt=0)


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    min_inventory: int = Field(default=0, ge=0)
    max_capacity: int = Field(default=1000, ge=1)
    min_pages_per_day: int | None = Field(default=None, gt=0)
