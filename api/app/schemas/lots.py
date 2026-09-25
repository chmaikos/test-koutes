from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.lots import LotEventType, LotPurgeCleanupStatus

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
    barcode: str
    name: str
    normalized_name: str
    version: int
    created_at: datetime
    updated_at: datetime
    physical_box_count: int
    box_count: int
    active_file_count: int
    archived_file_count: int
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
    barcode: str
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
    overwrite_archived_collisions: bool = False
    expected_collision_signature: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_collision_overwrite(self) -> LotMerge:
        if (
            not self.overwrite_archived_collisions
            and self.expected_collision_signature is not None
        ):
            raise ValueError(
                "expected_collision_signature is only allowed when "
                "overwrite_archived_collisions is true"
            )
        return self


class LotIdentityOut(BaseModel):
    id: int
    barcode: str
    name: str
    version: int


class LotArchivedBoxCollisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    box_number: str
    source_box_id: int
    source_box_archived: bool
    target_box_id: int
    target_box_archived: bool
    survivor_box_id: int
    survivor_lot_side: Literal["source", "target"]
    removed_box_id: int
    removed_lot_side: Literal["source", "target"]
    request_item_relink_count: int
    discrepancy_relink_count: int
    box_event_delete_count: int
    file_delete_count: int
    file_event_delete_count: int


class LotFileReferenceCollisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    normalized_reference: str
    source_file_ids: list[int]
    target_file_ids: list[int]
    active_file_ids: list[int]
    archived_only: bool
    survivor_file_id: int | None
    removed_file_ids: list[int]


class LotHardBoxOverlapOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    box_number: str
    source_box_ids: list[int]
    target_box_ids: list[int]
    source_active_box_ids: list[int]
    target_active_box_ids: list[int]


class LotPalletCollisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    normalized_pallet_number: str
    source_pallet_id: int
    source_pallet_number: str
    source_is_active: bool
    target_pallet_id: int
    target_pallet_number: str
    target_is_active: bool
    reason: Literal["inactive_target"]


class LotPalletMergeActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_pallet_id: int
    source_pallet_number: str
    source_is_active: bool
    action: Literal["combine", "transfer"]
    target_pallet_id: int | None
    box_count: int


class LotMergeCandidateOut(BaseModel):
    source: LotIdentityOut
    target: LotIdentityOut
    merge_allowed: bool
    overlapping_box_numbers: list[str]
    overlapping_box_count: int
    overlap_list_truncated: bool
    resolvable_archived_collisions: list[LotArchivedBoxCollisionOut]
    resolvable_archived_collision_count: int
    resolvable_archived_collisions_truncated: bool
    hard_overlaps: list[LotHardBoxOverlapOut]
    hard_overlap_count: int
    hard_overlaps_truncated: bool
    pallet_collisions: list[LotPalletCollisionOut]
    pallet_collision_count: int
    pallet_collisions_truncated: bool
    pallet_actions: list[LotPalletMergeActionOut]
    pallet_action_count: int
    pallet_actions_truncated: bool
    file_reference_collisions: list[LotFileReferenceCollisionOut]
    file_reference_collision_count: int
    file_reference_collisions_truncated: bool
    active_file_reference_collision_count: int
    archived_file_reference_collision_count: int
    merge_allowed_with_archived_overwrite: bool
    requires_explicit_overwrite: bool
    collision_signature: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class LotMergeOut(BaseModel):
    source: LotIdentityOut
    target: LotIdentityOut
    moved_box_count: int
    moved_request_item_count: int
    overwritten_archived_box_count: int = 0
    relinked_request_item_count: int = 0
    relinked_discrepancy_count: int = 0
    deleted_box_event_count: int = 0
    combined_pallet_count: int = 0
    moved_pallet_count: int = 0
    absorbed_pallet_ids: list[int] = Field(default_factory=list)
    moved_pallet_ids: list[int] = Field(default_factory=list)
    moved_file_count: int = 0
    overwritten_archived_file_count: int = 0
    deleted_file_event_count: int = 0


class LotMergeConflictOut(BaseModel):
    code: str
    message: str
    source: LotIdentityOut | None = None
    target: LotIdentityOut | None = None
    merge_candidate: LotMergeCandidateOut | None = None


class LotMergeConflictResponse(BaseModel):
    detail: LotMergeConflictOut


class LotRenameCurrentOut(BaseModel):
    id: int
    name: str
    normalized_name: str | None
    version: int
    updated_at: datetime


class LotRenameConflictOut(BaseModel):
    code: str
    message: str
    merge_candidate: LotMergeCandidateOut | None = None
    current: LotRenameCurrentOut | None = None


class LotRenameConflictResponse(BaseModel):
    detail: LotRenameConflictOut


class LotPurgeEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_type: str
    entity_id: int


class LotPurgeBlockerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    remediation: str
    entities: list[LotPurgeEntityOut]
    entity_count: int
    entities_truncated: bool


class LotPurgeRequestPreviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: int
    origin: str
    status: str
    direction: str
    item_count: int
    lot_item_count: int


class LotPurgePreviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lot_id: int
    lot_name: str
    lot_version: int
    active_box_count: int
    active_box_ids: list[int]
    active_box_ids_truncated: bool
    archived_box_count: int
    archived_box_ids: list[int]
    archived_box_ids_truncated: bool
    active_pallet_count: int
    active_pallet_ids: list[int]
    active_pallet_ids_truncated: bool
    archived_pallet_count: int
    archived_pallet_ids: list[int]
    archived_pallet_ids_truncated: bool
    linked_request_count: int
    linked_request_ids: list[int]
    linked_request_ids_truncated: bool
    requests: list[LotPurgeRequestPreviewOut]
    requests_truncated: bool
    object_key_count: int
    file_count: int
    active_file_count: int
    archived_file_count: int
    file_event_count: int
    linked_file_snapshot_count: int
    graph_signature: str
    eligible: bool
    blockers: list[LotPurgeBlockerOut]
    confirmation_policy: Literal[
        "exact_case_sensitive_no_normalization"
    ] = "exact_case_sensitive_no_normalization"


class LotPurge(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)
    expected_graph_signature: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="Optional preview graph hash; when supplied it must still match.",
    )


class LotPurgeObjectFailureOut(BaseModel):
    object_key: str
    error: str


class LotPurgeResultOut(BaseModel):
    purge_audit_id: int
    deleted_lot: LotIdentityOut
    archived_box_count: int
    pallet_count: int
    receipt_count: int
    object_key_count: int
    object_cleanup_status: LotPurgeCleanupStatus
    object_cleanup_failures: list[LotPurgeObjectFailureOut] = Field(
        default_factory=list
    )
    file_count: int = 0
    file_event_count: int = 0
    detached_file_snapshot_count: int = 0


class LotPurgeCleanupOut(BaseModel):
    purge_audit_id: int
    object_cleanup_status: LotPurgeCleanupStatus
    object_cleanup_failures: list[LotPurgeObjectFailureOut] = Field(
        default_factory=list
    )


class LotPurgeConflictOut(BaseModel):
    code: str
    message: str
    current_preview: LotPurgePreviewOut | None = None


class LotPurgeConflictResponse(BaseModel):
    detail: LotPurgeConflictOut


class LotForcePurgeBlockerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    message: str
    entity_ids: list[int]
    entity_count: int
    entity_ids_truncated: bool


class LotForcePurgeItemPositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_id: int
    before_position: int
    after_position: int | None


class LotForcePurgeRequestRewriteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: int
    adjustment_event_type: Literal["force_purge_adjusted"]
    origin: str
    status: str
    direction: str
    before_item_count: int
    after_item_count: int
    before_quantity: int
    after_quantity: int
    before_actual_received_quantity: int | None
    after_actual_received_quantity: int | None
    before_variance_quantity: int | None
    after_variance_quantity: int | None
    item_positions: list[LotForcePurgeItemPositionOut]
    item_positions_truncated: bool
    removed_item_ids: list[int]
    removed_item_count: int
    removed_item_ids_truncated: bool
    removed_discrepancy_ids: list[int]
    removed_discrepancy_count: int
    removed_discrepancy_ids_truncated: bool
    removed_discrepancy_photo_ids: list[int]
    removed_discrepancy_photo_count: int
    removed_discrepancy_photo_ids_truncated: bool
    preserved_sibling_lot_ids: list[int]
    preserved_sibling_lot_count: int
    preserved_sibling_lot_ids_truncated: bool


class LotForcePurgeLineageDetachOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: int
    field_name: Literal[
        "source_inbound_request_id",
        "parent_request_id",
        "root_request_id",
    ]
    deleted_target_request_id: int


class LotForcePurgeObjectCleanupPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deletable_key_count: int
    shared_skipped_key_count: int


class LotForcePurgePreviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lot_id: int
    lot_name: str
    lot_version: int
    confirmation_phrase: str
    active_box_ids: list[int]
    active_box_count: int
    active_box_ids_truncated: bool
    archived_box_ids: list[int]
    archived_box_count: int
    archived_box_ids_truncated: bool
    active_pallet_ids: list[int]
    active_pallet_count: int
    active_pallet_ids_truncated: bool
    archived_pallet_ids: list[int]
    archived_pallet_count: int
    archived_pallet_ids_truncated: bool
    touched_request_ids: list[int]
    touched_request_count: int
    touched_request_ids_truncated: bool
    request_rewrites: list[LotForcePurgeRequestRewriteOut]
    request_rewrite_count: int
    request_rewrites_truncated: bool
    fully_deleted_request_ids: list[int]
    fully_deleted_request_count: int
    fully_deleted_request_ids_truncated: bool
    incoming_lineage_detachments: list[LotForcePurgeLineageDetachOut]
    incoming_lineage_detachment_count: int
    incoming_lineage_detachments_truncated: bool
    file_count: int
    active_file_count: int
    archived_file_count: int
    file_event_count: int
    linked_file_snapshot_count: int
    object_cleanup: LotForcePurgeObjectCleanupPlanOut
    hard_blockers: list[LotForcePurgeBlockerOut]
    overridden_blockers: list[LotForcePurgeBlockerOut]
    graph_signature: str
    force_allowed: bool
    confirmation_policy: Literal[
        "exact_case_sensitive_no_normalization"
    ] = "exact_case_sensitive_no_normalization"


class LotForcePurge(BaseModel):
    confirmation_name: str = Field(min_length=1, max_length=64)
    confirmation_phrase: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=20, max_length=2000)
    expected_version: int = Field(ge=1)
    expected_graph_signature: str = Field(min_length=64, max_length=64)
    acknowledged_blocker_codes: list[str] = Field(max_length=100)


class LotForcePurgeResultOut(BaseModel):
    purge_audit_id: int
    deleted_lot: LotIdentityOut
    purge_mode: Literal["force"] = "force"
    active_box_count: int
    archived_box_count: int
    pallet_count: int
    touched_request_count: int
    rewritten_request_count: int
    deleted_request_count: int
    lineage_detachment_count: int
    deletable_object_count: int
    skipped_object_count: int
    object_cleanup_status: LotPurgeCleanupStatus
    object_cleanup_failure_count: int
    file_count: int = 0
    file_event_count: int = 0
    detached_file_snapshot_count: int = 0


class LotForcePurgeConflictOut(BaseModel):
    code: str
    message: str
    current_preview: LotForcePurgePreviewOut | None = None


class LotForcePurgeConflictResponse(BaseModel):
    detail: LotForcePurgeConflictOut


class MergedLotOut(BaseModel):
    state: Literal["merged"] = "merged"
    id: int
    barcode: str
    name: str
    version: int
    merged_at: datetime
    merged_by_user_id: int | None
    merged_into: LotIdentityOut


class BoxLotReassignment(BaseModel):
    lot_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)
    expected_lot_version: int = Field(ge=1)
    pallet_id: int | None = Field(default=None, ge=1)
    detach_pallet: bool = False

    @model_validator(mode="after")
    def validate_pallet_action(self) -> BoxLotReassignment:
        if self.pallet_id is not None and self.detach_pallet:
            raise ValueError("pallet_id and detach_pallet cannot both be supplied")
        return self


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
    "LotArchivedBoxCollisionOut",
    "LotHardBoxOverlapOut",
    "LotFileReferenceCollisionOut",
    "LotPalletCollisionOut",
    "LotPalletMergeActionOut",
    "LotMerge",
    "LotMergeCandidateOut",
    "LotMergeConflictOut",
    "LotMergeConflictResponse",
    "LotMergeOut",
    "MergedLotOut",
    "LotOptionOut",
    "LotForcePurge",
    "LotForcePurgeBlockerOut",
    "LotForcePurgeConflictOut",
    "LotForcePurgeConflictResponse",
    "LotForcePurgeItemPositionOut",
    "LotForcePurgeLineageDetachOut",
    "LotForcePurgeObjectCleanupPlanOut",
    "LotForcePurgePreviewOut",
    "LotForcePurgeRequestRewriteOut",
    "LotForcePurgeResultOut",
    "LotPurge",
    "LotPurgeBlockerOut",
    "LotPurgeCleanupOut",
    "LotPurgeConflictOut",
    "LotPurgeConflictResponse",
    "LotPurgeEntityOut",
    "LotPurgeObjectFailureOut",
    "LotPurgePreviewOut",
    "LotPurgeRequestPreviewOut",
    "LotPurgeResultOut",
    "LotProgressState",
    "LotRename",
    "LotRenameConflictOut",
    "LotRenameConflictResponse",
    "LotRenameCurrentOut",
    "LotSort",
    "LotSortField",
    "LotStatusCounts",
    "LotSummaryOut",
    "SortDirection",
]
