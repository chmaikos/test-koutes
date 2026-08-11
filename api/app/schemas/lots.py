from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.lots import LotEventType

LotProgressState = Literal["active", "in_progress", "complete", "no_eligible"]
LotSortField = Literal["name", "completion", "box_count", "last_activity"]
SortDirection = Literal["asc", "desc"]


class LotStatusCounts(BaseModel):
    quarantined: int = 0
    received: int = 0
    processing: int = 0
    incomplete: int = 0
    ready_to_return: int = 0
    returned: int = 0


class LotSummaryOut(BaseModel):
    id: int
    name: str
    normalized_name: str
    version: int
    created_at: datetime
    updated_at: datetime
    physical_box_count: int
    box_count: int
    status_counts: LotStatusCounts
    eligible_box_count: int
    completed_box_count: int
    completion_percent: float | None
    progress_state: LotProgressState
    warehouse_count: int
    warehouse_names: list[str]
    staged_receipt_count: int
    last_box_activity: datetime | None
    acl_scoped: bool
    scope_label: Literal["global", "accessible_warehouses_only"]


class LotEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lot_id: int
    event_type: LotEventType
    old_name: str | None
    new_name: str | None
    actor_user_id: int | None
    reason: str | None
    occurred_at: datetime
    metadata: dict[str, object] = Field(
        default_factory=dict, validation_alias="event_metadata"
    )


class LotDetailOut(LotSummaryOut):
    audit_history: list[LotEventOut] = Field(default_factory=list)
    audit_history_included: bool = False


class LotOptionOut(BaseModel):
    id: int
    name: str
    normalized_name: str
    exact_normalized_match: bool = False


class LotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    warehouse_id: int = Field(ge=1)


class LotRename(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)


class LotMerge(BaseModel):
    target_lot_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    expected_source_version: int = Field(ge=1)
    expected_target_version: int = Field(ge=1)


class LotIdentityOut(BaseModel):
    id: int
    name: str
    version: int


class LotMergeOut(BaseModel):
    source: LotIdentityOut
    target: LotIdentityOut
    moved_box_count: int
    moved_request_item_count: int


class MergedLotOut(BaseModel):
    state: Literal["merged"] = "merged"
    id: int
    name: str
    version: int
    merged_at: datetime
    merged_by_user_id: int | None
    merged_into: LotIdentityOut


class BoxLotReassignment(BaseModel):
    lot_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    expected_lot_version: int = Field(ge=1)


class LotFilters(BaseModel):
    search: str | None = None
    warehouse_id: int | None = None
    progress_state: LotProgressState | None = None


class LotSort(BaseModel):
    sort_by: LotSortField = "last_activity"
    sort_dir: SortDirection = "desc"


__all__ = [
    "BoxLotReassignment",
    "LotCreate",
    "LotDetailOut",
    "LotEventOut",
    "LotFilters",
    "LotIdentityOut",
    "LotMerge",
    "LotMergeOut",
    "MergedLotOut",
    "LotOptionOut",
    "LotProgressState",
    "LotRename",
    "LotSort",
    "LotSortField",
    "LotStatusCounts",
    "LotSummaryOut",
    "SortDirection",
]
