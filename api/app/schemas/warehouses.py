from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    min_inventory: int
    max_capacity: int


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    min_inventory: int | None = Field(default=None, ge=0)
    max_capacity: int | None = Field(default=None, ge=1)


class WarehouseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    min_inventory: int = Field(default=0, ge=0)
    max_capacity: int = Field(default=1000, ge=1)
