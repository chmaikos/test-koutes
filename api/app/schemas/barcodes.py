from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BarcodeKind = Literal["lot", "pallet", "box", "file"]


class BarcodeRedirectOut(BaseModel):
    entity_kind: BarcodeKind
    entity_id: int
    barcode: str
    frontend_path: str
    display_label: str


class BarcodeResolutionOut(BaseModel):
    entity_kind: BarcodeKind
    entity_id: int
    barcode: str
    lifecycle_state: Literal[
        "active",
        "archived",
        "returned",
        "merged",
        "absorbed",
        "retired",
    ]
    retired: bool
    frontend_path: str | None
    display_label: str
    hierarchy: dict[str, object] = Field(default_factory=dict)
    redirect: BarcodeRedirectOut | None = None
    retired_at: datetime | None = None
    retirement_reason: str | None = None
    retirement_operation: object | None = None
    retirement_metadata: dict[str, object] | None = None


class BarcodeFileMigrationAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: int
    identity_id: int
    issuance_number: int
    old_barcode: str | None
    new_barcode: str
    migrated_at: datetime


__all__ = [
    "BarcodeFileMigrationAuditOut",
    "BarcodeRedirectOut",
    "BarcodeResolutionOut",
]
