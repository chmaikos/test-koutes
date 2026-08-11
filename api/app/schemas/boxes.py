from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.boxes import BoxEventType, BoxStatus
from app.services.boxes import BoxRuleError, normalize_box_number


class BoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_number: str
    lot: str
    lot_id: int
    contents: str | None
    current_warehouse_id: int
    status: BoxStatus
    received_at: datetime | None
    processing_completed_at: datetime | None
    returned_at: datetime | None
    archived_at: datetime | None
    archived_by_user_id: int | None
    archive_reason: str | None
    created_at: datetime
    updated_at: datetime
    receipt_request_id: int | None = None


class BoxCreate(BaseModel):
    box_number: str = Field(min_length=1, max_length=64)
    lot: str | None = Field(default=None, min_length=1, max_length=64)
    lot_id: int | None = Field(default=None, ge=1)
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

    @model_validator(mode="after")
    def _one_lot_identity(self) -> BoxCreate:
        if (self.lot is None) == (self.lot_id is None):
            raise ValueError("provide exactly one of lot or lot_id")
        return self


class BoxUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BoxStatus | None = None
    warehouse_id: int | None = Field(default=None, ge=1)
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
    metadata: dict[str, object] = Field(
        default_factory=dict, validation_alias="event_metadata"
    )


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
    cancelled_request_ids: list[int] = Field(default_factory=list)


class BoxDeleteRequest(BaseModel):
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)


class BoxDeleteResult(BaseModel):
    box_id: int
    archived: bool
    cancelled_request_ids: list[int] = Field(default_factory=list)


class BulkDeleteRequest(BaseModel):
    box_ids: list[int] = Field(min_length=1, max_length=500)
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)


class BulkDeleteResult(BaseModel):
    deleted_ids: list[int]
    archived_ids: list[int] = Field(default_factory=list)
    cancelled_request_ids: list[int] = Field(default_factory=list)
    skipped: list[BulkSkip]


class ImportSkip(BaseModel):
    row: int
    box_number: str | None
    reason: str


class MappedImportItem(BaseModel):
    box_number: str = Field(min_length=1, max_length=64)
    lot: str = Field(min_length=1, max_length=64)
    contents: str | None = Field(default=None, max_length=200)


class MappedImportRequest(BaseModel):
    warehouse_id: int = Field(ge=1)
    items: list[MappedImportItem] = Field(min_length=1, max_length=5000)
    restore_archived: bool = False


class ImportResult(BaseModel):
    created: list[BoxOut]
    restored: list[BoxOut] = Field(default_factory=list)
    skipped: list[ImportSkip]
    receipt_request_ids: list[int] = Field(default_factory=list)
    staged_receipt_ids: list[int] = Field(default_factory=list)


class StagedReceiptResult(BaseModel):
    outcome: str = "staged"
    staged_receipt_id: int


class QuarantineBulkAction(BaseModel):
    box_ids: list[int] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class QuarantineBulkResult(BaseModel):
    updated: list[BoxOut] = Field(default_factory=list)
    archived_ids: list[int] = Field(default_factory=list)
    skipped: list[BulkSkip] = Field(default_factory=list)
    request_ids: list[int] = Field(default_factory=list)
