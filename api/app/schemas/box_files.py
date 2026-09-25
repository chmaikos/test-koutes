from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.box_files import BoxFileEventType, clean_box_file_reference
from app.models.boxes import BoxStatus


class FileInput(BaseModel):
    """Reusable structured file value accepted by box intake workflows."""

    model_config = ConfigDict(extra="forbid")

    reference: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)

    @field_validator("reference")
    @classmethod
    def _clean_reference(cls, value: str) -> str:
        return clean_box_file_reference(value)


class FileSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reference: str
    description: str | None
    barcode: str
    position: int
    version: int


class FileListOut(BaseModel):
    id: int
    reference: str
    description: str | None
    barcode: str
    position: int
    lot_id: int
    lot: str
    pallet_id: int | None
    pallet: str | None
    box_id: int
    box: str
    warehouse_id: int
    warehouse: str
    status: BoxStatus
    is_active: bool
    archived_at: datetime | None
    archived_by_user_id: int | None
    archive_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    created_by_user_id: int | None
    updated_by_user_id: int | None


class FileDetailOut(FileListOut):
    pass


class FileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    box_id: int = Field(ge=1)
    reference: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    position: int | None = Field(default=None, ge=1)

    @field_validator("reference")
    @classmethod
    def _clean_reference(cls, value: str) -> str:
        return clean_box_file_reference(value)


class FileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reference: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reference", mode="before")
    @classmethod
    def _clean_reference(cls, value: object) -> str:
        if value is None:
            raise ValueError("reference cannot be null")
        if not isinstance(value, str):
            raise ValueError("reference must be a string")
        return clean_box_file_reference(value)


class FileMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    box_id: int = Field(ge=1)
    position: int | None = Field(default=None, ge=1)
    expected_version: int = Field(ge=1)
    force: bool = False
    reason: str | None = Field(default=None, max_length=2000)


class FileArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    force: bool = False

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must not be blank")
        return cleaned


class FileRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    position: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    force: bool = False

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("reason must not be blank")
        return cleaned


class FileEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    event_type: BoxFileEventType
    before_snapshot: dict[str, object] | None
    after_snapshot: dict[str, object] | None
    actor_user_id: int | None
    reason: str | None
    occurred_at: datetime
    metadata: dict[str, object] = Field(
        default_factory=dict, validation_alias="event_metadata"
    )


class FileIntegrityIdGroup(BaseModel):
    count: int
    file_ids: list[int] = Field(default_factory=list)
    snapshot_ids: list[int] = Field(default_factory=list)


class FileIntegrityDuplicateGroup(BaseModel):
    lot_id: int
    normalized_reference: str
    file_ids: list[int]


class FileIntegrityDuplicates(BaseModel):
    count: int
    groups: list[FileIntegrityDuplicateGroup]


class FileIntegrityOut(BaseModel):
    safe: bool
    conflict_count: int
    cross_lot_placements: FileIntegrityIdGroup
    duplicate_normalized_references: FileIntegrityDuplicates
    invalid_positions: FileIntegrityIdGroup
    archived_files_in_active_workflows: FileIntegrityIdGroup
    detached_tracked_snapshots: FileIntegrityIdGroup


FileActivity = Literal["active", "archived", "all"]
FileSortField = Literal[
    "reference",
    "lot",
    "pallet",
    "box",
    "warehouse",
    "status",
    "position",
    "created_at",
    "updated_at",
]


__all__ = [
    "FileActivity",
    "FileArchive",
    "FileCreate",
    "FileDetailOut",
    "FileEventOut",
    "FileInput",
    "FileIntegrityOut",
    "FileListOut",
    "FileMove",
    "FileRestore",
    "FileSummaryOut",
    "FileSortField",
    "FileUpdate",
]
