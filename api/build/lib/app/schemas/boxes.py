from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.boxes import BoxEventType, BoxStatus


class BoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_number: str
    owner: str
    current_warehouse_id: int
    status: BoxStatus
    received_at: datetime | None
    processing_completed_at: datetime | None
    returned_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BoxCreate(BaseModel):
    box_number: str = Field(min_length=1, max_length=64)
    owner: str = Field(default="", max_length=200)
    warehouse_id: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2000)


class BoxUpdate(BaseModel):
    status: BoxStatus | None = None
    warehouse_id: int | None = Field(default=None, ge=1)
    owner: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class BoxEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_id: int
    warehouse_id: int
    event_type: BoxEventType
    from_status: BoxStatus | None
    to_status: BoxStatus | None
    from_warehouse_id: int | None
    to_warehouse_id: int | None
    occurred_at: datetime
    user_id: int | None
    note: str | None
