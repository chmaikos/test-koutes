from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.boxes import BoxEventType, BoxStatus
from app.services.boxes import BoxRuleError, normalize_box_number


class BoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_number: str
    lot: str
    contents: str | None
    current_warehouse_id: int
    status: BoxStatus
    received_at: datetime | None
    processing_completed_at: datetime | None
    returned_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BoxCreate(BaseModel):
    box_number: str = Field(min_length=1, max_length=64)
    lot: str = Field(min_length=1, max_length=64)
    contents: str | None = Field(default=None, max_length=200)
    warehouse_id: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("box_number")
    @classmethod
    def _normalize_box_number(cls, value: str) -> str:
        # Single source of truth for the box-number rule lives in
        # ``services.boxes.normalize_box_number``; here we just translate
        # the domain error into Pydantic's value-error contract so the
        # caller sees a 422 with a clear field-scoped message.
        try:
            return normalize_box_number(value)
        except BoxRuleError as exc:
            raise ValueError(str(exc)) from exc


class BoxUpdate(BaseModel):
    status: BoxStatus | None = None
    warehouse_id: int | None = Field(default=None, ge=1)
    lot: str | None = Field(default=None, min_length=1, max_length=64)
    contents: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    force: bool = False


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


# --- bulk + import -----------------------------------------------------------


class BulkBoxUpdate(BaseModel):
    box_ids: list[int] = Field(min_length=1, max_length=500)
    warehouse_id: int | None = Field(default=None, ge=1)
    status: BoxStatus | None = None
    note: str | None = Field(default=None, max_length=2000)
    force: bool = False


class BulkSkip(BaseModel):
    box_id: int
    box_number: str
    reason: str


class BulkResult(BaseModel):
    updated: list[BoxOut]
    skipped: list[BulkSkip]


class BulkDeleteRequest(BaseModel):
    box_ids: list[int] = Field(min_length=1, max_length=500)


class BulkDeleteResult(BaseModel):
    deleted_ids: list[int]
    skipped: list[BulkSkip]


class ImportSkip(BaseModel):
    row: int
    box_number: str | None
    reason: str


class ImportResult(BaseModel):
    created: list[BoxOut]
    skipped: list[ImportSkip]
