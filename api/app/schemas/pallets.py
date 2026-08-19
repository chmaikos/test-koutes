from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.pallets import PalletEventType

PalletProgressState = Literal["active", "in_progress", "complete", "no_eligible"]
PalletSortField = Literal[
    "pallet_number", "completion", "box_count", "latest_activity"
]


class PalletStatusCounts(BaseModel):
    quarantined: int = 0
    received: int = 0
    processing: int = 0
    incomplete: int = 0
    ready_to_return: int = 0
    returned: int = 0


class PalletSummaryOut(BaseModel):
    id: int
    lot_id: int
    lot_name: str
    current_warehouse_id: int
    warehouse_name: str
    pallet_number: str
    normalized_pallet_number: str
    version: int
    is_active: bool
    archived_at: datetime | None
    archived_by_user_id: int | None
    archive_reason: str | None
    absorbed_into_pallet_id: int | None
    absorbed_at: datetime | None
    absorbed_by_user_id: int | None
    created_at: datetime
    updated_at: datetime
    created_by_user_id: int | None
    updated_by_user_id: int | None
    physical_box_count: int
    box_count: int
    status_counts: PalletStatusCounts
    eligible_box_count: int
    completed_box_count: int
    completion_percent: float | None
    progress_state: PalletProgressState
    latest_activity: datetime


class PalletDetailOut(PalletSummaryOut):
    pass


class PalletOptionOut(BaseModel):
    id: int
    pallet_number: str
    normalized_pallet_number: str
    lot_id: int
    lot_name: str
    current_warehouse_id: int
    warehouse_name: str
    is_active: bool
    exact_normalized_match: bool = False


class PalletCreate(BaseModel):
    lot_id: int = Field(ge=1)
    warehouse_id: int = Field(ge=1)
    pallet_number: str = Field(min_length=1, max_length=64)


class PalletRename(BaseModel):
    new_pallet_number: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)


class PalletStateChange(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)


class PalletBoxMutation(BaseModel):
    box_ids: list[int] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class PalletBoxMutationOut(BaseModel):
    pallet_id: int
    updated_box_ids: list[int] = Field(default_factory=list)
    skipped: list[dict[str, object]] = Field(default_factory=list)
    cancelled_request_ids: list[int] = Field(default_factory=list)


class PalletMove(BaseModel):
    warehouse_id: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)
    force: bool = False
    expected_version: int = Field(ge=1)


class PalletMoveOut(BaseModel):
    pallet_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    affected_box_count: int
    box_ids: list[int]
    cancelled_request_ids: list[int]
    version: int


class PalletIntegrityGroup(BaseModel):
    count: int
    box_ids: list[int]


class PalletIntegrityOut(BaseModel):
    orphaned_pallet_ids: PalletIntegrityGroup
    cross_lot: PalletIntegrityGroup
    cross_warehouse: PalletIntegrityGroup
    inactive_pallet_assignments: PalletIntegrityGroup
    unassigned_active_boxes: PalletIntegrityGroup


class PalletEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pallet_id: int
    event_type: PalletEventType
    old_pallet_number: str | None
    new_pallet_number: str | None
    from_warehouse_id: int | None
    to_warehouse_id: int | None
    actor_user_id: int | None
    reason: str | None
    occurred_at: datetime
    metadata: dict[str, object] = Field(
        default_factory=dict, validation_alias="event_metadata"
    )


class PalletConflictCurrentOut(BaseModel):
    id: int
    pallet_number: str
    normalized_pallet_number: str
    version: int
    is_active: bool
    updated_at: datetime


class PalletConflictOut(BaseModel):
    code: str
    message: str
    current: PalletConflictCurrentOut | None = None
    box_count: int | None = None


class PalletConflictResponse(BaseModel):
    detail: PalletConflictOut


__all__ = [
    "PalletConflictOut",
    "PalletConflictResponse",
    "PalletCreate",
    "PalletDetailOut",
    "PalletBoxMutation",
    "PalletBoxMutationOut",
    "PalletEventOut",
    "PalletOptionOut",
    "PalletIntegrityOut",
    "PalletMove",
    "PalletMoveOut",
    "PalletProgressState",
    "PalletRename",
    "PalletSortField",
    "PalletStateChange",
    "PalletStatusCounts",
    "PalletSummaryOut",
]
