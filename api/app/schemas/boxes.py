from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.boxes import BoxEventType, BoxStatus
from app.models.pallets import clean_optional_pallet_number
from app.schemas.box_files import FileInput, FileSummaryOut
from app.services.boxes import BoxRuleError, normalize_box_number


class BoxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    box_number: str
    lot: str
    lot_id: int
    pallet_id: int | None
    pallet_number: str | None
    contents: str | None
    file_count: int = 0
    active_file_count: int = 0
    archived_file_count: int = 0
    files: list[FileSummaryOut] = Field(default_factory=list, validation_alias="active_files")
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
    pallet_number: str | None = Field(default=None, max_length=64)
    pallet_id: int | None = Field(default=None, ge=1)
    contents: str | None = Field(default=None, max_length=2000)
    files: list[FileInput] | None = Field(default=None, max_length=5000)
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

    @field_validator("pallet_number", mode="before")
    @classmethod
    def _normalize_pallet_number(cls, value: object) -> object:
        return (
            clean_optional_pallet_number(value)
            if value is None or isinstance(value, str)
            else value
        )

    @model_validator(mode="after")
    def _valid_identities(self) -> BoxCreate:
        if (self.lot is None) == (self.lot_id is None):
            raise ValueError("provide exactly one of lot or lot_id")
        if self.pallet_id is not None and self.pallet_number is None:
            raise ValueError("pallet_id requires pallet_number")
        return self


class BoxUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: BoxStatus | None = None
    warehouse_id: int | None = Field(default=None, ge=1)
    contents: str | None = Field(default=None, max_length=2000)
    files: list[FileInput] | None = Field(default=None, max_length=5000)
    note: str | None = Field(default=None, max_length=2000)
    pallet_id: int | None = Field(default=None, ge=1)
    detach_pallet: bool = False
    force: bool = False

    @model_validator(mode="after")
    def _pallet_action_is_unambiguous(self) -> BoxUpdate:
        if self.pallet_id is not None and self.detach_pallet:
            raise ValueError("pallet_id and detach_pallet cannot both be supplied")
        return self


class BoxUpdateResult(BoxOut):
    cancelled_request_ids: list[int] = Field(default_factory=list)
    detached_pallet_id: int | None = None


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
    pallet_number: str | None = Field(default=None, max_length=64)
    pallet_id: int | None = Field(default=None, ge=1)
    contents: str | None = Field(default=None, max_length=2000)
    files: list[FileInput] | None = Field(default=None, max_length=5000)

    @field_validator("pallet_number", mode="before")
    @classmethod
    def _normalize_pallet_number(cls, value: object) -> object:
        return (
            clean_optional_pallet_number(value)
            if value is None or isinstance(value, str)
            else value
        )

    @model_validator(mode="after")
    def _pallet_id_requires_number(self) -> MappedImportItem:
        if self.pallet_id is not None and self.pallet_number is None:
            raise ValueError("pallet_id requires pallet_number")
        return self


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
