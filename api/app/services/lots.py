"""Domain operations for first-class lot identities."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    Float,
    and_,
    case,
    cast,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    union_all,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.barcode_identities import BarcodeIdentity
from app.models.box_files import BoxFile, BoxFileEvent, BoxFileEventType
from app.models.boxes import (
    ACTIVE_STATUSES,
    Box,
    BoxEvent,
    BoxEventType,
    BoxStatus,
)
from app.models.lots import (
    MAX_LOT_NAME_LENGTH,
    Lot,
    LotEvent,
    LotEventType,
    LotPurgeCleanupStatus,
    LotPurgeEvent,
    clean_lot_name,
    normalize_lot_name,
)
from app.models.notifications import InAppNotification, RequestEmailOutbox
from app.models.pallets import Pallet, PalletEvent, PalletEventType
from app.models.requests import (
    BoxRequest,
    BoxRequestAttachment,
    BoxRequestComment,
    BoxRequestDirection,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyPhoto,
    BoxRequestDiscrepancyType,
    BoxRequestDocument,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestException,
    BoxRequestItem,
    BoxRequestItemFileSnapshot,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.acl import allowed_warehouse_ids, can_access
from app.services.barcodes import (
    barcode_retirement_target,
    issue_barcode_identity,
    retire_barcode_identities,
)
from app.services.object_storage import delete_document_strict


class LotRuleError(ValueError):
    """Base class for lot errors that routers may map to client responses."""


class LotAccessError(LotRuleError):
    """The actor is not authorized to perform the lot operation."""


class LotNotFoundError(LotRuleError):
    """The requested lot identity does not exist."""


class LotConflictError(LotRuleError):
    """The requested operation conflicts with an existing lot identity."""


class LotVersionConflictError(LotConflictError):
    """The caller supplied a stale optimistic-lock version."""


LotMergeSide = Literal["source", "target"]


@dataclass(frozen=True)
class LotArchivedBoxCollision:
    box_number: str
    source_box_id: int
    source_box_archived: bool
    target_box_id: int
    target_box_archived: bool
    survivor_box_id: int
    survivor_lot_side: LotMergeSide
    removed_box_id: int
    removed_lot_side: LotMergeSide
    request_item_relink_count: int
    discrepancy_relink_count: int
    box_event_delete_count: int
    file_delete_count: int
    file_event_delete_count: int


@dataclass(frozen=True)
class LotFileReferenceCollision:
    normalized_reference: str
    source_file_ids: list[int]
    target_file_ids: list[int]
    active_file_ids: list[int]
    archived_only: bool
    survivor_file_id: int | None
    removed_file_ids: list[int]


@dataclass(frozen=True)
class LotHardBoxOverlap:
    box_number: str
    source_box_ids: list[int]
    target_box_ids: list[int]
    source_active_box_ids: list[int]
    target_active_box_ids: list[int]


@dataclass(frozen=True)
class LotPalletCollision:
    normalized_pallet_number: str
    source_pallet_id: int
    source_pallet_number: str
    source_is_active: bool
    target_pallet_id: int
    target_pallet_number: str
    target_is_active: bool
    reason: Literal["inactive_target"]


@dataclass(frozen=True)
class LotPalletMergeAction:
    source_pallet_id: int
    source_pallet_number: str
    source_is_active: bool
    action: Literal["combine", "transfer"]
    target_pallet_id: int | None = None
    box_count: int = 0


@dataclass(frozen=True)
class LotMergeCandidate:
    source_id: int
    source_barcode: str
    source_name: str
    source_version: int
    target_id: int
    target_barcode: str
    target_name: str
    target_version: int
    merge_allowed: bool
    overlapping_box_numbers: list[str]
    overlapping_box_count: int
    overlap_list_truncated: bool
    resolvable_archived_collisions: list[LotArchivedBoxCollision]
    resolvable_archived_collision_count: int
    resolvable_archived_collisions_truncated: bool
    hard_overlaps: list[LotHardBoxOverlap]
    hard_overlap_count: int
    hard_overlaps_truncated: bool
    merge_allowed_with_archived_overwrite: bool
    requires_explicit_overwrite: bool
    collision_signature: str
    pallet_collisions: list[LotPalletCollision]
    pallet_collision_count: int
    pallet_collisions_truncated: bool
    pallet_actions: list[LotPalletMergeAction]
    pallet_action_count: int
    pallet_actions_truncated: bool
    file_reference_collisions: list[LotFileReferenceCollision]
    file_reference_collision_count: int
    file_reference_collisions_truncated: bool
    active_file_reference_collision_count: int
    archived_file_reference_collision_count: int
    all_resolvable_archived_collisions: list[LotArchivedBoxCollision] = field(
        default_factory=list,
        repr=False,
    )
    all_archived_file_collisions: list[LotFileReferenceCollision] = field(
        default_factory=list,
        repr=False,
    )


class LotNameCollisionError(LotConflictError):
    """A rename resolved to another active canonical lot."""

    def __init__(self, message: str, candidate: LotMergeCandidate):
        super().__init__(message)
        self.candidate = candidate


class LotMergeConflictError(LotConflictError):
    """A merge failed its transactional integrity checks."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        source: Lot | None = None,
        target: Lot | None = None,
        candidate: LotMergeCandidate | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.source = source
        self.target = target
        self.candidate = candidate


@dataclass(frozen=True)
class LotMergeResult:
    source: Lot
    target: Lot
    moved_box_count: int
    moved_request_item_count: int
    warehouse_ids: list[int]
    overwritten_archived_box_count: int = 0
    relinked_request_item_count: int = 0
    relinked_discrepancy_count: int = 0
    deleted_box_event_count: int = 0
    combined_pallet_count: int = 0
    moved_pallet_count: int = 0
    absorbed_pallet_ids: list[int] = field(default_factory=list)
    moved_pallet_ids: list[int] = field(default_factory=list)
    survivor_box_ids: list[int] = field(default_factory=list)
    removed_box_ids: list[int] = field(default_factory=list)
    moved_file_count: int = 0
    overwritten_archived_file_count: int = 0
    deleted_file_event_count: int = 0


LotProgressState = Literal["active", "in_progress", "complete", "no_eligible"]
LotSortField = Literal["name", "completion", "box_count", "last_activity"]
SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class LotSummary:
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
    status_counts: dict[str, int]
    eligible_box_count: int
    completed_box_count: int
    completion_percent: float | None
    progress_state: LotProgressState
    warehouse_count: int
    warehouse_names: list[str]
    staged_receipt_count: int
    last_box_activity: datetime | None
    acl_scoped: bool
    scope_label: str


@dataclass(frozen=True)
class LotOption:
    id: int
    barcode: str
    name: str
    normalized_name: str
    exact_normalized_match: bool


class LotPurgeAnalysisError(LotRuleError):
    """Base error for purge preview and locked revalidation."""


class LotPurgeNotFoundError(LotPurgeAnalysisError):
    """The requested lot does not exist, including as a merge tombstone."""


class LotPurgeConflictError(LotPurgeAnalysisError):
    """A purge confirmation or locked graph no longer matches."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        preview: LotPurgeEligibility | LotForcePurgeImpactPlan | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.preview = preview


@dataclass(frozen=True)
class LotPurgeEntity:
    entity_type: str
    entity_id: int


@dataclass(frozen=True)
class LotPurgeBlocker:
    code: str
    message: str
    remediation: str
    entities: list[LotPurgeEntity]
    entity_count: int
    entities_truncated: bool


@dataclass(frozen=True)
class LotPurgeRequestPreview:
    request_id: int
    origin: str
    status: str
    direction: str
    item_count: int
    lot_item_count: int


@dataclass(frozen=True)
class LotPurgeEligibility:
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
    requests: list[LotPurgeRequestPreview]
    requests_truncated: bool
    object_key_count: int
    file_count: int
    active_file_count: int
    archived_file_count: int
    file_event_count: int
    linked_file_snapshot_count: int
    graph_signature: str
    eligible: bool
    blockers: list[LotPurgeBlocker]


@dataclass(frozen=True)
class LotPurgeCleanupResult:
    audit_id: int
    status: LotPurgeCleanupStatus
    failures: list[dict[str, object]]


@dataclass(frozen=True)
class LotPurgeResult:
    audit_id: int
    lot_id: int
    lot_name: str
    lot_version: int
    archived_box_count: int
    pallet_count: int
    receipt_count: int
    object_key_count: int
    object_cleanup_status: LotPurgeCleanupStatus
    object_cleanup_failures: list[dict[str, object]]
    warehouse_ids: list[int]
    file_count: int = 0
    file_event_count: int = 0
    detached_file_snapshot_count: int = 0


@dataclass(frozen=True)
class LotForcePurgeResult:
    audit_id: int
    lot_id: int
    lot_name: str
    lot_version: int
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
    object_cleanup_failures: list[dict[str, object]]
    warehouse_ids: list[int]
    file_count: int = 0
    file_event_count: int = 0
    detached_file_snapshot_count: int = 0


@dataclass(frozen=True)
class LotForcePurgeItemPosition:
    item_id: int
    before_position: int
    after_position: int | None


@dataclass(frozen=True)
class LotForcePurgeRequestRewrite:
    request_id: int
    adjustment_event_type: str
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
    item_positions: list[LotForcePurgeItemPosition]
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


@dataclass(frozen=True)
class LotForcePurgeLineageDetach:
    request_id: int
    field_name: Literal[
        "source_inbound_request_id",
        "parent_request_id",
        "root_request_id",
    ]
    deleted_target_request_id: int


@dataclass(frozen=True)
class LotForcePurgeObjectCleanupPlan:
    deletable_keys: list[str]
    deletable_key_count: int
    deletable_keys_truncated: bool
    shared_skipped_keys: list[str]
    shared_skipped_key_count: int
    shared_skipped_keys_truncated: bool


@dataclass(frozen=True)
class LotForcePurgeBlocker:
    code: str
    message: str
    entity_ids: list[int]
    entity_count: int
    entity_ids_truncated: bool


@dataclass(frozen=True)
class LotForcePurgeImpactPlan:
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
    request_rewrites: list[LotForcePurgeRequestRewrite]
    request_rewrite_count: int
    request_rewrites_truncated: bool
    fully_deleted_request_ids: list[int]
    fully_deleted_request_count: int
    fully_deleted_request_ids_truncated: bool
    incoming_lineage_detachments: list[LotForcePurgeLineageDetach]
    incoming_lineage_detachment_count: int
    incoming_lineage_detachments_truncated: bool
    file_count: int
    active_file_count: int
    archived_file_count: int
    file_event_count: int
    linked_file_snapshot_count: int
    object_cleanup: LotForcePurgeObjectCleanupPlan
    hard_blockers: list[LotForcePurgeBlocker]
    overridden_blockers: list[LotForcePurgeBlocker]
    graph_signature: str
    force_allowed: bool


def validate_lot_name(value: str) -> str:
    """Return the sole canonical display form used by backend workflows."""
    try:
        return clean_lot_name(value)
    except (AttributeError, TypeError, ValueError) as exc:
        message = str(exc) or "lot is required"
        raise LotRuleError(message) from exc


def _purge_file_counts(
    db: Session,
    *,
    box_ids: list[int],
    lock_for_update: bool = False,
) -> dict[str, int]:
    if not box_ids:
        return {
            "file_count": 0,
            "active_file_count": 0,
            "archived_file_count": 0,
            "file_event_count": 0,
            "linked_file_snapshot_count": 0,
        }
    file_stmt = (
        select(BoxFile)
        .where(BoxFile.box_id.in_(box_ids))
        .order_by(BoxFile.id)
    )
    if lock_for_update:
        file_stmt = file_stmt.with_for_update(of=BoxFile)
    files = list(db.scalars(file_stmt).all())
    file_ids = [file.id for file in files]
    if lock_for_update and file_ids:
        list(
            db.scalars(
                select(BoxFileEvent)
                .where(BoxFileEvent.file_id.in_(file_ids))
                .order_by(BoxFileEvent.id)
                .with_for_update(of=BoxFileEvent)
            ).all()
        )
        list(
            db.scalars(
                select(BoxRequestItemFileSnapshot)
                .where(BoxRequestItemFileSnapshot.file_id.in_(file_ids))
                .order_by(BoxRequestItemFileSnapshot.id)
                .with_for_update(of=BoxRequestItemFileSnapshot)
            ).all()
        )
    file_event_count = (
        int(
            db.scalar(
                select(func.count(BoxFileEvent.id)).where(
                    BoxFileEvent.file_id.in_(file_ids)
                )
            )
            or 0
        )
        if file_ids
        else 0
    )
    linked_snapshot_count = (
        int(
            db.scalar(
                select(func.count(BoxRequestItemFileSnapshot.id)).where(
                    BoxRequestItemFileSnapshot.file_id.in_(file_ids)
                )
            )
            or 0
        )
        if file_ids
        else 0
    )
    return {
        "file_count": len(files),
        "active_file_count": sum(file.archived_at is None for file in files),
        "archived_file_count": sum(
            file.archived_at is not None for file in files
        ),
        "file_event_count": file_event_count,
        "linked_file_snapshot_count": linked_snapshot_count,
    }


def _active_lot_use_statement(lot_ids: list[int]):
    """Build the deterministic PostgreSQL FOR SHARE identity-use lock."""
    return (
        select(Lot)
        .where(
            Lot.id.in_(sorted(set(lot_ids))),
            Lot.merged_into_lot_id.is_(None),
        )
        .order_by(Lot.id)
        .with_for_update(read=True)
    )


def _exclusive_lot_statement(lot_ids: list[int]):
    """Build the deterministic exclusive lock used by identity mutations."""
    return (
        select(Lot)
        .where(Lot.id.in_(sorted(set(lot_ids))))
        .order_by(Lot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_active_lots_for_use(db: Session, lot_ids: list[int]) -> dict[int, Lot]:
    """Hold shared locks until transaction end before writing Lot references.

    PostgreSQL compiles ``read=True`` as ``FOR SHARE``. SQLite omits the lock
    clause but still executes the active-state validation used by tests.
    """
    ordered_ids = sorted(set(lot_ids))
    if not ordered_ids:
        return {}
    locked = {
        lot.id: lot
        for lot in db.scalars(_active_lot_use_statement(ordered_ids)).all()
    }
    missing = [lot_id for lot_id in ordered_ids if lot_id not in locked]
    if missing:
        rendered = ", ".join(str(lot_id) for lot_id in missing)
        raise LotConflictError(
            f"lot identity is unavailable or already merged: {rendered}"
        )
    return locked


def lock_lots_exclusively(db: Session, lot_ids: list[int]) -> dict[int, Lot]:
    """Lock Lot rows exclusively in ascending id order."""
    ordered_ids = sorted(set(lot_ids))
    return {
        lot.id: lot
        for lot in db.scalars(_exclusive_lot_statement(ordered_ids)).all()
    }


def find_lot(db: Session, name: str, *, lock_for_use: bool = False) -> Lot | None:
    """Find a lot by trim/collapse/case-insensitive identity."""
    try:
        normalized = normalize_lot_name(name)
    except (AttributeError, TypeError, ValueError) as exc:
        raise LotRuleError(str(exc) or "lot is required") from exc
    lot = db.scalar(
        select(Lot).where(
            Lot.normalized_name == normalized,
            Lot.merged_into_lot_id.is_(None),
        )
    )
    if lot is not None and lock_for_use:
        return lock_active_lots_for_use(db, [lot.id])[lot.id]
    return lot


def get_lot(db: Session, lot_id: int, *, lock_for_use: bool = False) -> Lot:
    if lock_for_use:
        try:
            return lock_active_lots_for_use(db, [lot_id])[lot_id]
        except LotConflictError as exc:
            raise LotNotFoundError(f"active lot {lot_id} not found") from exc
    lot = db.scalar(
        select(Lot).where(
            Lot.id == lot_id,
            Lot.merged_into_lot_id.is_(None),
        )
    )
    if lot is None:
        raise LotNotFoundError(f"lot {lot_id} not found")
    return lot


def _ensure_receipt_access(user: User, warehouse_id: int | None) -> None:
    if warehouse_id is not None and not can_access(user, warehouse_id):
        raise LotAccessError(f"no access to warehouse {warehouse_id}")


def get_or_create_lot_result(
    db: Session,
    *,
    user: User,
    name: str,
    warehouse_id: int | None = None,
    lock_for_use: bool = True,
) -> tuple[Lot, bool]:
    """Resolve a canonical lot, safely recovering from racing inserts.

    The savepoint keeps a uniqueness race local: a losing insert rolls back only
    its candidate row and audit event, not the surrounding receipt/import.
    """
    _ensure_receipt_access(user, warehouse_id)
    cleaned = validate_lot_name(name)
    normalized = normalize_lot_name(cleaned)
    existing = find_lot(db, cleaned, lock_for_use=lock_for_use)
    if existing is not None:
        return existing, False

    candidate = Lot(
        name=cleaned,
        normalized_name=normalized,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    try:
        with db.begin_nested():
            issue_barcode_identity(
                db,
                candidate,
                actor_user_id=user.id,
                reason="Lot created while resolving a receipt or import.",
                metadata={
                    "operation": "get_or_create",
                    "warehouse_id": warehouse_id,
                },
            )
            db.add(candidate)
            db.flush()
            db.add(
                LotEvent(
                    lot_id=candidate.id,
                    event_type=LotEventType.created,
                    new_name=candidate.name,
                    actor_user_id=user.id,
                    reason="Created while resolving an authorized receipt/import.",
                    event_metadata={
                        "operation": "get_or_create",
                        "warehouse_id": warehouse_id,
                    },
                )
            )
            db.flush()
    except IntegrityError:
        existing = find_lot(db, cleaned, lock_for_use=lock_for_use)
        if existing is None:
            raise LotConflictError(
                f"lot {cleaned!r} was created concurrently; retry the operation"
            ) from None
        return existing, False
    return candidate, True


def get_or_create_lot(
    db: Session,
    *,
    user: User,
    name: str,
    warehouse_id: int | None = None,
    lock_for_use: bool = True,
) -> Lot:
    lot, _created = get_or_create_lot_result(
        db,
        user=user,
        name=name,
        warehouse_id=warehouse_id,
        lock_for_use=lock_for_use,
    )
    return lot


def resolve_lot_names_for_use(
    db: Session,
    *,
    user: User,
    names: list[str],
    warehouse_id: int | None = None,
) -> dict[str, Lot]:
    """Resolve a batch, then acquire all shared identity locks by Lot id."""
    cleaned_by_normalized = {
        normalize_lot_name(name): validate_lot_name(name) for name in names
    }
    resolved: dict[str, Lot] = {}
    for normalized in sorted(cleaned_by_normalized):
        lot = get_or_create_lot(
            db,
            user=user,
            name=cleaned_by_normalized[normalized],
            warehouse_id=warehouse_id,
            lock_for_use=False,
        )
        resolved[normalized] = lot
    locked = lock_active_lots_for_use(
        db,
        [lot.id for lot in resolved.values()],
    )
    return {
        normalized: locked[lot.id]
        for normalized, lot in resolved.items()
    }


def resolve_lot(
    db: Session,
    *,
    user: User,
    lot: str | None = None,
    lot_id: int | None = None,
    warehouse_id: int | None = None,
    create: bool = True,
    lock_for_use: bool = True,
) -> Lot:
    """Resolve exactly one name/id input for a warehouse-authorized workflow."""
    _ensure_receipt_access(user, warehouse_id)
    if (lot is None) == (lot_id is None):
        raise LotRuleError("provide exactly one of lot or lot_id")
    if lot_id is not None:
        return get_lot(db, lot_id, lock_for_use=lock_for_use)
    assert lot is not None
    if create:
        return get_or_create_lot(
            db,
            user=user,
            name=lot,
            warehouse_id=warehouse_id,
            lock_for_use=lock_for_use,
        )
    existing = find_lot(db, lot, lock_for_use=lock_for_use)
    if existing is None:
        raise LotNotFoundError(f"lot {validate_lot_name(lot)!r} not found")
    return existing


_PURGE_LIST_LIMIT = 100
_PURGE_RECEIPT_ORIGINS = {
    BoxRequestOrigin.manual_entry,
    BoxRequestOrigin.xlsx_import,
}


def _purge_lot_lock_statement(lot_id: int):
    return (
        select(Lot)
        .where(Lot.id == lot_id)
        .with_for_update(of=Lot)
    )


def _purge_box_lock_statement(lot_id: int):
    return (
        select(Box)
        .where(Box.lot_id == lot_id)
        .order_by(Box.id)
        .with_for_update(of=Box)
    )


def _purge_pallet_lock_statement(lot_id: int):
    return (
        select(Pallet)
        .where(Pallet.lot_id == lot_id)
        .order_by(Pallet.id)
        .with_for_update(of=Pallet)
    )


def _purge_request_lock_statement(request_ids: list[int]):
    return (
        select(BoxRequest.id)
        .where(BoxRequest.id.in_(sorted(set(request_ids))))
        .order_by(BoxRequest.id)
        .with_for_update(of=BoxRequest)
    )


def _purge_request_item_lock_statement(request_ids: list[int]):
    return (
        select(BoxRequestItem.id)
        .where(BoxRequestItem.request_id.in_(sorted(set(request_ids))))
        .order_by(BoxRequestItem.id)
        .with_for_update(of=BoxRequestItem)
    )


def _purge_owned_lock_statements(
    *,
    lot_id: int,
    box_ids: list[int],
    request_ids: list[int],
    discrepancy_ids: list[int],
    pallet_ids: list[int] | None = None,
) -> list:
    """Build explicit PostgreSQL lock targets for every purge-owned table."""
    statements = []
    if request_ids:
        statements.append(
            select(BoxRequestDiscrepancy.id)
            .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
            .order_by(BoxRequestDiscrepancy.id)
            .with_for_update(of=BoxRequestDiscrepancy)
        )
        for model in (
            BoxRequestEvent,
            BoxRequestException,
            BoxRequestDocument,
            BoxRequestComment,
            BoxRequestAttachment,
            InAppNotification,
            RequestEmailOutbox,
        ):
            statements.append(
                select(model.id)
                .where(model.request_id.in_(request_ids))
                .order_by(model.id)
                .with_for_update(of=model)
            )
        if discrepancy_ids:
            statements.append(
                select(BoxRequestDiscrepancyPhoto.id)
                .where(
                    BoxRequestDiscrepancyPhoto.discrepancy_id.in_(discrepancy_ids)
                )
                .order_by(BoxRequestDiscrepancyPhoto.id)
                .with_for_update(of=BoxRequestDiscrepancyPhoto)
            )
    if box_ids:
        statements.append(
            select(BoxEvent.id)
            .where(BoxEvent.box_id.in_(box_ids))
            .order_by(BoxEvent.id)
            .with_for_update(of=BoxEvent)
        )
    if pallet_ids:
        statements.append(
            select(PalletEvent.id)
            .where(PalletEvent.pallet_id.in_(pallet_ids))
            .order_by(PalletEvent.id)
            .with_for_update(of=PalletEvent)
        )
    statements.append(
        select(LotEvent.id)
        .where(LotEvent.lot_id == lot_id)
        .order_by(LotEvent.id)
        .with_for_update(of=LotEvent)
    )
    return statements


def _lock_purge_owned_rows(
    db: Session,
    *,
    lot_id: int,
    box_ids: list[int],
    request_ids: list[int],
    pallet_ids: list[int] | None = None,
) -> None:
    """Lock every row whose mutable state or object key is purge-owned."""
    discrepancy_ids = (
        list(
            db.scalars(
                select(BoxRequestDiscrepancy.id)
                .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
                .order_by(BoxRequestDiscrepancy.id)
            ).all()
        )
        if request_ids
        else []
    )
    for statement in _purge_owned_lock_statements(
        lot_id=lot_id,
        box_ids=box_ids,
        request_ids=request_ids,
        discrepancy_ids=discrepancy_ids,
        pallet_ids=pallet_ids,
    ):
        db.execute(statement).all()


def _bounded_ids(ids: list[int]) -> tuple[list[int], bool]:
    ordered = sorted(set(ids))
    return ordered[:_PURGE_LIST_LIMIT], len(ordered) > _PURGE_LIST_LIMIT


def _purge_entities(entity_type: str, ids: list[int]) -> list[LotPurgeEntity]:
    bounded, _truncated = _bounded_ids(ids)
    return [
        LotPurgeEntity(entity_type=entity_type, entity_id=entity_id)
        for entity_id in bounded
    ]


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value))


def _bounded_strings(values: list[str]) -> tuple[list[str], bool]:
    ordered = sorted(set(values))
    return ordered[:_PURGE_LIST_LIMIT], len(ordered) > _PURGE_LIST_LIMIT


def _force_purge_lot_lock_statement(lot_id: int):
    return (
        select(Lot)
        .where(Lot.id == lot_id)
        .with_for_update(of=Lot)
    )


def _force_purge_box_lock_statement(lot_id: int):
    return (
        select(Box)
        .where(Box.lot_id == lot_id)
        .order_by(Box.id)
        .with_for_update(of=Box)
    )


def _force_purge_pallet_lock_statement(lot_id: int):
    return _purge_pallet_lock_statement(lot_id)


def _force_purge_request_lock_statement(request_ids: list[int]):
    return (
        select(BoxRequest.id)
        .where(BoxRequest.id.in_(sorted(set(request_ids))))
        .order_by(BoxRequest.id)
        .with_for_update(of=BoxRequest)
    )


def _force_purge_item_lock_statement(request_ids: list[int]):
    return (
        select(BoxRequestItem.id)
        .where(BoxRequestItem.request_id.in_(sorted(set(request_ids))))
        .order_by(BoxRequestItem.id)
        .with_for_update(of=BoxRequestItem)
    )


def _force_purge_discrepancy_lock_statement(request_ids: list[int]):
    return (
        select(BoxRequestDiscrepancy.id)
        .where(BoxRequestDiscrepancy.request_id.in_(sorted(set(request_ids))))
        .order_by(BoxRequestDiscrepancy.id)
        .with_for_update(of=BoxRequestDiscrepancy)
    )


def _force_blocker(blocker: LotPurgeBlocker) -> LotForcePurgeBlocker:
    entity_ids = [entity.entity_id for entity in blocker.entities]
    return LotForcePurgeBlocker(
        code=blocker.code,
        message=blocker.message,
        entity_ids=entity_ids,
        entity_count=blocker.entity_count,
        entity_ids_truncated=blocker.entities_truncated,
    )


def _force_object_key_reference_rows(db: Session, object_keys: list[str]):
    if not object_keys:
        return []
    references = union_all(
        select(
            literal("document").label("row_type"),
            BoxRequestDocument.id.label("row_id"),
            BoxRequestDocument.request_id.label("request_id"),
            BoxRequestDocument.object_key.label("object_key"),
        ),
        select(
            literal("attachment").label("row_type"),
            BoxRequestAttachment.id.label("row_id"),
            BoxRequestAttachment.request_id.label("request_id"),
            BoxRequestAttachment.object_key.label("object_key"),
        ),
        select(
            literal("discrepancy_photo").label("row_type"),
            BoxRequestDiscrepancyPhoto.id.label("row_id"),
            BoxRequestDiscrepancy.request_id.label("request_id"),
            BoxRequestDiscrepancyPhoto.object_key.label("object_key"),
        ).join(
            BoxRequestDiscrepancy,
            BoxRequestDiscrepancy.id
            == BoxRequestDiscrepancyPhoto.discrepancy_id,
        ),
    ).subquery()
    return db.execute(
        select(
            references.c.row_type,
            references.c.row_id,
            references.c.request_id,
            references.c.object_key,
        )
        .where(references.c.object_key.in_(object_keys))
        .order_by(
            references.c.object_key,
            references.c.row_type,
            references.c.row_id,
        )
    ).all()


def analyze_lot_force_purge_impact(
    db: Session,
    *,
    lot_id: int,
    lock_for_update: bool = False,
) -> LotForcePurgeImpactPlan:
    """Plan a surgical, single-Lot force purge without mutating the graph.

    The selected Lot and all of its active and archived boxes are in scope.
    Request items are in scope when either their ``lot_id`` matches the Lot or
    their ``box_id`` points at one of those boxes. Requests with surviving items
    are rewritten; only requests with no survivors are planned for full removal.
    """
    lot = db.scalar(
        _force_purge_lot_lock_statement(lot_id)
        if lock_for_update
        else select(Lot).where(Lot.id == lot_id)
    )
    if lot is None:
        raise LotPurgeNotFoundError(f"lot {lot_id} not found")

    pallets = list(
        db.scalars(
            _force_purge_pallet_lock_statement(lot_id)
            if lock_for_update
            else select(Pallet).where(Pallet.lot_id == lot_id).order_by(Pallet.id)
        ).all()
    )
    pallet_ids = [pallet.id for pallet in pallets]
    boxes = list(
        db.scalars(
            _force_purge_box_lock_statement(lot_id)
            if lock_for_update
            else select(Box).where(Box.lot_id == lot_id).order_by(Box.id)
        ).all()
    )
    box_ids = [box.id for box in boxes]
    box_id_set = set(box_ids)
    file_counts = _purge_file_counts(
        db,
        box_ids=box_ids,
        lock_for_update=lock_for_update,
    )
    removed_item_filter = BoxRequestItem.lot_id == lot_id
    if box_ids:
        removed_item_filter = or_(
            removed_item_filter,
            BoxRequestItem.box_id.in_(box_ids),
        )
    selected_item = removed_item_filter
    if pallet_ids:
        selected_item = or_(
            selected_item,
            BoxRequestItem.pallet_id.in_(pallet_ids),
        )
    touched_request_ids_query = select(BoxRequestItem.request_id).where(selected_item)
    if box_ids:
        touched_request_ids_query = union_all(
            touched_request_ids_query,
            select(BoxRequestDiscrepancy.request_id).where(
                BoxRequestDiscrepancy.box_id.in_(box_ids)
            ),
        )
    touched_request_ids_subquery = touched_request_ids_query.subquery()
    touched_request_ids_all = [
        int(request_id)
        for request_id in db.scalars(
            select(touched_request_ids_subquery.c.request_id)
            .distinct()
            .order_by(touched_request_ids_subquery.c.request_id)
        ).all()
    ]

    preliminary_deleted_request_ids: list[int] = []
    preliminary_incoming_request_ids: list[int] = []
    if lock_for_update and touched_request_ids_all:
        item_counts = db.execute(
            select(
                BoxRequestItem.request_id,
                func.count(BoxRequestItem.id).label("item_count"),
                func.sum(case((removed_item_filter, 1), else_=0)).label(
                    "selected_item_count"
                ),
            )
            .where(
                BoxRequestItem.request_id.in_(touched_request_ids_all)
            )
            .group_by(BoxRequestItem.request_id)
            .order_by(BoxRequestItem.request_id)
        ).all()
        preliminary_deleted_request_ids = [
            int(row.request_id)
            for row in item_counts
            if int(row.item_count) == int(row.selected_item_count or 0)
        ]
        if preliminary_deleted_request_ids:
            preliminary_incoming_request_ids = [
                int(request_id)
                for request_id in db.scalars(
                    select(BoxRequest.id)
                    .where(
                        BoxRequest.id.not_in(
                            preliminary_deleted_request_ids
                        ),
                        or_(
                            BoxRequest.source_inbound_request_id.in_(
                                preliminary_deleted_request_ids
                            ),
                            BoxRequest.parent_request_id.in_(
                                preliminary_deleted_request_ids
                            ),
                            BoxRequest.root_request_id.in_(
                                preliminary_deleted_request_ids
                            ),
                        ),
                    )
                    .order_by(BoxRequest.id)
                ).all()
            ]
        db.execute(
            _force_purge_request_lock_statement(
                touched_request_ids_all
                + preliminary_incoming_request_ids
            )
        ).all()
        db.execute(_force_purge_item_lock_statement(touched_request_ids_all)).all()
        db.execute(
            _force_purge_discrepancy_lock_statement(touched_request_ids_all)
        ).all()
    if lock_for_update:
        _lock_purge_owned_rows(
            db,
            lot_id=lot_id,
            box_ids=box_ids,
            request_ids=touched_request_ids_all,
            pallet_ids=pallet_ids,
        )

    requests = (
        list(
            db.scalars(
                select(BoxRequest)
                .where(BoxRequest.id.in_(touched_request_ids_all))
                .order_by(BoxRequest.id)
            ).all()
        )
        if touched_request_ids_all
        else []
    )
    items = (
        list(
            db.scalars(
                select(BoxRequestItem)
                .where(BoxRequestItem.request_id.in_(touched_request_ids_all))
                .order_by(
                    BoxRequestItem.request_id,
                    BoxRequestItem.position,
                    BoxRequestItem.id,
                )
            ).all()
        )
        if touched_request_ids_all
        else []
    )
    discrepancies = (
        list(
            db.scalars(
                select(BoxRequestDiscrepancy)
                .where(
                    BoxRequestDiscrepancy.request_id.in_(
                        touched_request_ids_all
                    )
                )
                .order_by(
                    BoxRequestDiscrepancy.request_id,
                    BoxRequestDiscrepancy.id,
                )
            ).all()
        )
        if touched_request_ids_all
        else []
    )
    discrepancy_ids = [discrepancy.id for discrepancy in discrepancies]
    photo_rows = (
        db.execute(
            select(
                BoxRequestDiscrepancyPhoto.id,
                BoxRequestDiscrepancyPhoto.discrepancy_id,
                BoxRequestDiscrepancyPhoto.object_key,
            )
            .where(
                BoxRequestDiscrepancyPhoto.discrepancy_id.in_(discrepancy_ids)
            )
            .order_by(
                BoxRequestDiscrepancyPhoto.discrepancy_id,
                BoxRequestDiscrepancyPhoto.id,
            )
        ).all()
        if discrepancy_ids
        else []
    )

    items_by_request: dict[int, list[BoxRequestItem]] = {
        request_id: [] for request_id in touched_request_ids_all
    }
    for item in items:
        items_by_request[item.request_id].append(item)
    discrepancies_by_request: dict[int, list[BoxRequestDiscrepancy]] = {
        request_id: [] for request_id in touched_request_ids_all
    }
    for discrepancy in discrepancies:
        discrepancies_by_request[discrepancy.request_id].append(discrepancy)
    photos_by_discrepancy: dict[int, list[tuple[int, str]]] = {
        discrepancy_id: [] for discrepancy_id in discrepancy_ids
    }
    for photo_id, discrepancy_id, object_key in photo_rows:
        photos_by_discrepancy[int(discrepancy_id)].append(
            (int(photo_id), str(object_key))
        )

    removed_item_ids_all = {
        item.id
        for item in items
        if item.lot_id == lot_id
        or (item.box_id is not None and item.box_id in box_id_set)
    }
    fully_deleted_request_ids_all: list[int] = []
    rewrite_rows: list[
        tuple[
            BoxRequest,
            list[BoxRequestItem],
            list[BoxRequestItem],
            list[BoxRequestDiscrepancy],
        ]
    ] = []
    for request in requests:
        request_items = items_by_request[request.id]
        removed_items = [
            item for item in request_items if item.id in removed_item_ids_all
        ]
        remaining_items = [
            item for item in request_items if item.id not in removed_item_ids_all
        ]
        request_discrepancies = discrepancies_by_request[request.id]
        if removed_items and not remaining_items:
            fully_deleted_request_ids_all.append(request.id)
            removed_discrepancies = request_discrepancies
        else:
            removed_discrepancies = [
                discrepancy
                for discrepancy in request_discrepancies
                if discrepancy.request_item_id in removed_item_ids_all
                or (
                    discrepancy.box_id is not None
                    and discrepancy.box_id in box_id_set
                )
            ]
            if removed_items or removed_discrepancies:
                rewrite_rows.append(
                    (
                        request,
                        removed_items,
                        remaining_items,
                        removed_discrepancies,
                    )
                )
    deleted_request_id_set = set(fully_deleted_request_ids_all)
    incoming_requests = (
        list(
            db.scalars(
                select(BoxRequest)
                .where(
                    BoxRequest.id.not_in(fully_deleted_request_ids_all),
                    or_(
                        BoxRequest.source_inbound_request_id.in_(
                            fully_deleted_request_ids_all
                        ),
                        BoxRequest.parent_request_id.in_(
                            fully_deleted_request_ids_all
                        ),
                        BoxRequest.root_request_id.in_(
                            fully_deleted_request_ids_all
                        ),
                    ),
                )
                .order_by(BoxRequest.id)
            ).all()
        )
        if fully_deleted_request_ids_all
        else []
    )
    incoming_request_ids = [request.id for request in incoming_requests]
    graph_changed_while_locking = False
    if lock_for_update:
        graph_changed_while_locking = (
            preliminary_deleted_request_ids
            != fully_deleted_request_ids_all
            or preliminary_incoming_request_ids != incoming_request_ids
        )
        extra_incoming_ids = sorted(
            set(incoming_request_ids)
            - set(preliminary_incoming_request_ids)
        )
        if extra_incoming_ids:
            db.execute(
                _force_purge_request_lock_statement(extra_incoming_ids)
            ).all()
    if lock_for_update and incoming_request_ids:
        # Re-read locked rows so the plan and signature use locked values.
        incoming_requests = list(
            db.scalars(
                select(BoxRequest)
                .where(BoxRequest.id.in_(incoming_request_ids))
                .order_by(BoxRequest.id)
            ).all()
        )

    lineage_detachments_all: list[LotForcePurgeLineageDetach] = []
    lineage_fields = (
        "source_inbound_request_id",
        "parent_request_id",
        "root_request_id",
    )
    for request in incoming_requests:
        for field_name in lineage_fields:
            target_id = getattr(request, field_name)
            if target_id in deleted_request_id_set:
                lineage_detachments_all.append(
                    LotForcePurgeLineageDetach(
                        request_id=request.id,
                        field_name=field_name,
                        deleted_target_request_id=int(target_id),
                    )
                )

    request_rewrites_all: list[LotForcePurgeRequestRewrite] = []
    for request, removed_items, remaining_items, removed_discrepancies in rewrite_rows:
        removed_ids = [item.id for item in removed_items]
        _bounded_removed_ids, removed_ids_truncated = _bounded_ids(removed_ids)
        removed_discrepancy_ids = [
            discrepancy.id for discrepancy in removed_discrepancies
        ]
        (
            _bounded_removed_discrepancy_ids,
            removed_discrepancy_ids_truncated,
        ) = _bounded_ids(removed_discrepancy_ids)
        removed_photo_ids = sorted(
            {
                photo_id
                for discrepancy in removed_discrepancies
                for photo_id, _object_key in photos_by_discrepancy.get(
                    discrepancy.id, []
                )
            }
        )
        _bounded_photo_ids, photo_ids_truncated = _bounded_ids(removed_photo_ids)
        remaining_positions = {
            item.id: position
            for position, item in enumerate(remaining_items, start=1)
        }
        position_plan_all = [
            LotForcePurgeItemPosition(
                item_id=item.id,
                before_position=item.position,
                after_position=remaining_positions.get(item.id),
            )
            for item in items_by_request[request.id]
        ]
        sibling_lot_ids = sorted(
            {
                int(item.lot_id)
                for item in remaining_items
                if item.lot_id is not None and item.lot_id != lot_id
            }
        )
        _bounded_sibling_ids, sibling_ids_truncated = _bounded_ids(
            sibling_lot_ids
        )
        after_quantity = max(1, request.quantity - len(removed_items))
        if request.status == BoxRequestStatus.completed:
            if request.direction == BoxRequestDirection.inbound:
                # Completed inbound items are the boxes actually received. The
                # requested quantity may be larger or smaller when the receipt
                # completed with a variance, so subtracting the removed actual
                # items from both sides preserves that variance exactly.
                after_actual = len(remaining_items)
            else:
                missing_item_ids = {
                    discrepancy.request_item_id
                    for discrepancy in discrepancies_by_request[request.id]
                    if (
                        discrepancy.discrepancy_type
                        == BoxRequestDiscrepancyType.missing
                        and discrepancy.request_item_id is not None
                    )
                }
                removed_actual_count = sum(
                    item.id not in missing_item_ids for item in removed_items
                )
                after_actual = max(
                    0,
                    (request.actual_received_quantity or 0)
                    - removed_actual_count,
                )
            after_variance = after_actual - after_quantity
        else:
            after_actual = request.actual_received_quantity
            after_variance = request.variance_quantity
        request_rewrites_all.append(
            LotForcePurgeRequestRewrite(
                request_id=request.id,
                adjustment_event_type=(
                    BoxRequestEventType.force_purge_adjusted.value
                ),
                origin=_enum_text(request.origin),
                status=_enum_text(request.status),
                direction=_enum_text(request.direction),
                before_item_count=len(items_by_request[request.id]),
                after_item_count=len(remaining_items),
                before_quantity=request.quantity,
                after_quantity=after_quantity,
                before_actual_received_quantity=request.actual_received_quantity,
                after_actual_received_quantity=after_actual,
                before_variance_quantity=request.variance_quantity,
                after_variance_quantity=after_variance,
                item_positions=position_plan_all,
                item_positions_truncated=(
                    len(position_plan_all) > _PURGE_LIST_LIMIT
                ),
                removed_item_ids=sorted(removed_ids),
                removed_item_count=len(removed_ids),
                removed_item_ids_truncated=removed_ids_truncated,
                removed_discrepancy_ids=sorted(removed_discrepancy_ids),
                removed_discrepancy_count=len(removed_discrepancy_ids),
                removed_discrepancy_ids_truncated=(
                    removed_discrepancy_ids_truncated
                ),
                removed_discrepancy_photo_ids=removed_photo_ids,
                removed_discrepancy_photo_count=len(removed_photo_ids),
                removed_discrepancy_photo_ids_truncated=photo_ids_truncated,
                preserved_sibling_lot_ids=sibling_lot_ids,
                preserved_sibling_lot_count=len(sibling_lot_ids),
                preserved_sibling_lot_ids_truncated=sibling_ids_truncated,
            )
        )

    document_rows = (
        db.execute(
            select(
                BoxRequestDocument.id,
                BoxRequestDocument.request_id,
                BoxRequestDocument.object_key,
            )
            .where(
                BoxRequestDocument.request_id.in_(
                    fully_deleted_request_ids_all
                )
            )
            .order_by(BoxRequestDocument.id)
        ).all()
        if fully_deleted_request_ids_all
        else []
    )
    attachment_rows = (
        db.execute(
            select(
                BoxRequestAttachment.id,
                BoxRequestAttachment.request_id,
                BoxRequestAttachment.object_key,
            )
            .where(
                BoxRequestAttachment.request_id.in_(
                    fully_deleted_request_ids_all
                )
            )
            .order_by(BoxRequestAttachment.id)
        ).all()
        if fully_deleted_request_ids_all
        else []
    )
    fully_deleted_discrepancy_ids = {
        discrepancy.id
        for request_id in fully_deleted_request_ids_all
        for discrepancy in discrepancies_by_request[request_id]
    }
    partially_removed_photo_ids = {
        photo_id
        for rewrite in request_rewrites_all
        for photo_id in rewrite.removed_discrepancy_photo_ids
    }
    deleted_photo_rows = [
        (int(photo_id), int(discrepancy_id), str(object_key))
        for photo_id, discrepancy_id, object_key in photo_rows
        if (
            int(discrepancy_id) in fully_deleted_discrepancy_ids
            or int(photo_id) in partially_removed_photo_ids
        )
    ]
    deleted_object_rows: set[tuple[str, int]] = {
        ("document", int(row.id)) for row in document_rows
    } | {
        ("attachment", int(row.id)) for row in attachment_rows
    } | {
        ("discrepancy_photo", photo_id)
        for photo_id, _discrepancy_id, _object_key in deleted_photo_rows
    }
    candidate_object_keys = sorted(
        {
            str(row.object_key) for row in document_rows
        }
        | {str(row.object_key) for row in attachment_rows}
        | {
            object_key
            for _photo_id, _discrepancy_id, object_key in deleted_photo_rows
        }
    )
    object_reference_rows = _force_object_key_reference_rows(
        db, candidate_object_keys
    )
    shared_keys = {
        str(row.object_key)
        for row in object_reference_rows
        if (str(row.row_type), int(row.row_id)) not in deleted_object_rows
    }
    deletable_keys_all = sorted(set(candidate_object_keys) - shared_keys)
    shared_keys_all = sorted(shared_keys)
    _bounded_deletable_keys, deletable_keys_truncated = _bounded_strings(
        deletable_keys_all
    )
    _bounded_shared_keys, shared_keys_truncated = _bounded_strings(
        shared_keys_all
    )
    object_cleanup = LotForcePurgeObjectCleanupPlan(
        deletable_keys=deletable_keys_all,
        deletable_key_count=len(deletable_keys_all),
        deletable_keys_truncated=deletable_keys_truncated,
        shared_skipped_keys=shared_keys_all,
        shared_skipped_key_count=len(shared_keys_all),
        shared_skipped_keys_truncated=shared_keys_truncated,
    )

    safe_preview = analyze_lot_purge_eligibility(db, lot_id=lot_id)
    hard_codes = {"merged_tombstone", "merge_target"}
    hard_blockers = [
        _force_blocker(blocker)
        for blocker in safe_preview.blockers
        if blocker.code in hard_codes
    ]
    overridden_blockers = [
        _force_blocker(blocker)
        for blocker in safe_preview.blockers
        if blocker.code not in hard_codes and blocker.code != "shared_object_key"
    ]
    if lot.merged_into_lot_id is None and lot.normalized_name is None:
        hard_blockers.append(
            LotForcePurgeBlocker(
                code="invalid_identity",
                message="The active Lot has no canonical identity.",
                entity_ids=[lot.id],
                entity_count=1,
                entity_ids_truncated=False,
            )
        )
    if graph_changed_while_locking:
        hard_blockers.append(
            LotForcePurgeBlocker(
                code="graph_changed",
                message=(
                    "The force-purge graph changed while locks were acquired; "
                    "retry the analysis."
                ),
                entity_ids=[],
                entity_count=0,
                entity_ids_truncated=False,
            )
        )

    def graph_rows(statement) -> list[list[object]]:
        return [list(row) for row in db.execute(statement).all()]

    graph: dict[str, object] = {
        "lot": [
            lot.id,
            lot.name,
            lot.normalized_name,
            lot.version,
            lot.merged_into_lot_id,
            lot.merged_at,
        ],
        "merge_sources": graph_rows(
            select(
                Lot.id,
                Lot.version,
                Lot.merged_into_lot_id,
                Lot.merged_at,
            )
            .where(Lot.merged_into_lot_id == lot.id)
            .order_by(Lot.id)
        ),
        "boxes": [
            [
                box.id,
                box.lot_id,
                box.pallet_id,
                box.current_warehouse_id,
                box.status,
                box.archived_at,
                box.updated_at,
            ]
            for box in boxes
        ],
        "files": (
            graph_rows(
                select(
                    BoxFile.id,
                    BoxFile.lot_id,
                    BoxFile.box_id,
                    BoxFile.normalized_reference,
                    BoxFile.position,
                    BoxFile.archived_at,
                    BoxFile.version,
                )
                .where(BoxFile.box_id.in_(box_ids))
                .order_by(BoxFile.id)
            )
            if box_ids
            else []
        ),
        "file_events": (
            graph_rows(
                select(
                    BoxFileEvent.id,
                    BoxFileEvent.file_id,
                    BoxFileEvent.event_type,
                    BoxFileEvent.occurred_at,
                )
                .where(
                    BoxFileEvent.file_id.in_(
                        select(BoxFile.id).where(
                            BoxFile.box_id.in_(box_ids)
                        )
                    )
                )
                .order_by(BoxFileEvent.id)
            )
            if box_ids
            else []
        ),
        "file_snapshot_links": (
            graph_rows(
                select(
                    BoxRequestItemFileSnapshot.id,
                    BoxRequestItemFileSnapshot.request_item_id,
                    BoxRequestItemFileSnapshot.file_id,
                )
                .where(
                    BoxRequestItemFileSnapshot.file_id.in_(
                        select(BoxFile.id).where(
                            BoxFile.box_id.in_(box_ids)
                        )
                    )
                )
                .order_by(BoxRequestItemFileSnapshot.id)
            )
            if box_ids
            else []
        ),
        "pallets": [_pallet_signature_value(pallet) for pallet in pallets],
        "pallet_events": (
            graph_rows(
                select(
                    PalletEvent.id,
                    PalletEvent.pallet_id,
                    PalletEvent.event_type,
                    PalletEvent.old_pallet_number,
                    PalletEvent.new_pallet_number,
                    PalletEvent.from_warehouse_id,
                    PalletEvent.to_warehouse_id,
                    PalletEvent.event_metadata,
                )
                .where(PalletEvent.pallet_id.in_(pallet_ids))
                .order_by(PalletEvent.id)
            )
            if pallet_ids
            else []
        ),
        "requests": [
            [
                request.id,
                request.version,
                request.status,
                request.origin,
                request.direction,
                request.quantity,
                request.actual_received_quantity,
                request.variance_quantity,
                request.source_inbound_request_id,
                request.parent_request_id,
                request.root_request_id,
            ]
            for request in requests
        ],
        "items": [
            [
                item.id,
                item.request_id,
                item.position,
                item.box_id,
                item.lot_id,
                item.pallet_id,
                item.lot,
                item.pallet,
                item.box_number,
            ]
            for item in items
        ],
        "discrepancies": [
            [
                discrepancy.id,
                discrepancy.request_id,
                discrepancy.request_item_id,
                discrepancy.box_id,
                discrepancy.discrepancy_type,
                discrepancy.quantity,
            ]
            for discrepancy in discrepancies
        ],
        "discrepancy_photos": [
            [int(photo_id), int(discrepancy_id), str(object_key)]
            for photo_id, discrepancy_id, object_key in photo_rows
        ],
        "incoming_requests": [
            [
                request.id,
                request.version,
                request.source_inbound_request_id,
                request.parent_request_id,
                request.root_request_id,
            ]
            for request in incoming_requests
        ],
        "request_events": (
            graph_rows(
                select(
                    BoxRequestEvent.id,
                    BoxRequestEvent.request_id,
                    BoxRequestEvent.event_type,
                    BoxRequestEvent.from_status,
                    BoxRequestEvent.to_status,
                    BoxRequestEvent.event_metadata,
                )
                .where(
                    BoxRequestEvent.request_id.in_(touched_request_ids_all)
                )
                .order_by(BoxRequestEvent.id)
            )
            if touched_request_ids_all
            else []
        ),
        "request_exceptions": (
            graph_rows(
                select(
                    BoxRequestException.id,
                    BoxRequestException.request_id,
                    BoxRequestException.exception_kind,
                    BoxRequestException.resolved_at,
                )
                .where(
                    BoxRequestException.request_id.in_(
                        touched_request_ids_all
                    )
                )
                .order_by(BoxRequestException.id)
            )
            if touched_request_ids_all
            else []
        ),
        "documents": [list(row) for row in document_rows],
        "attachments": [list(row) for row in attachment_rows],
        "comments": (
            graph_rows(
                select(BoxRequestComment.id, BoxRequestComment.request_id)
                .where(
                    BoxRequestComment.request_id.in_(touched_request_ids_all)
                )
                .order_by(BoxRequestComment.id)
            )
            if touched_request_ids_all
            else []
        ),
        "notifications": (
            graph_rows(
                select(InAppNotification.id, InAppNotification.request_id)
                .where(
                    InAppNotification.request_id.in_(
                        touched_request_ids_all
                    )
                )
                .order_by(InAppNotification.id)
            )
            if touched_request_ids_all
            else []
        ),
        "email_outbox": (
            graph_rows(
                select(RequestEmailOutbox.id, RequestEmailOutbox.request_id)
                .where(
                    RequestEmailOutbox.request_id.in_(
                        touched_request_ids_all
                    )
                )
                .order_by(RequestEmailOutbox.id)
            )
            if touched_request_ids_all
            else []
        ),
        "box_events": (
            graph_rows(
                select(
                    BoxEvent.id,
                    BoxEvent.box_id,
                    BoxEvent.event_type,
                    BoxEvent.from_status,
                    BoxEvent.to_status,
                    BoxEvent.from_warehouse_id,
                    BoxEvent.to_warehouse_id,
                )
                .where(BoxEvent.box_id.in_(box_ids))
                .order_by(BoxEvent.id)
            )
            if box_ids
            else []
        ),
        "lot_events": graph_rows(
            select(
                LotEvent.id,
                LotEvent.event_type,
                LotEvent.old_name,
                LotEvent.new_name,
                LotEvent.event_metadata,
            )
            .where(LotEvent.lot_id == lot.id)
            .order_by(LotEvent.id)
        ),
        "object_references": [list(row) for row in object_reference_rows],
        "fully_deleted_request_ids": fully_deleted_request_ids_all,
        "lineage_detachments": [
            [
                detachment.request_id,
                detachment.field_name,
                detachment.deleted_target_request_id,
            ]
            for detachment in lineage_detachments_all
        ],
    }
    encoded = json.dumps(graph, default=str, sort_keys=True, separators=(",", ":"))
    graph_signature = hashlib.sha256(encoded.encode()).hexdigest()

    active_box_ids_all = [
        box.id for box in boxes if box.archived_at is None
    ]
    archived_box_ids_all = [
        box.id for box in boxes if box.archived_at is not None
    ]
    active_pallet_ids_all = [
        pallet.id for pallet in pallets if pallet.is_active
    ]
    archived_pallet_ids_all = [
        pallet.id for pallet in pallets if not pallet.is_active
    ]
    _active_box_ids, active_box_ids_truncated = _bounded_ids(
        active_box_ids_all
    )
    _archived_box_ids, archived_box_ids_truncated = _bounded_ids(
        archived_box_ids_all
    )
    _active_pallet_ids, active_pallet_ids_truncated = _bounded_ids(
        active_pallet_ids_all
    )
    _archived_pallet_ids, archived_pallet_ids_truncated = _bounded_ids(
        archived_pallet_ids_all
    )
    _touched_request_ids, touched_request_ids_truncated = _bounded_ids(
        touched_request_ids_all
    )
    _fully_deleted_request_ids, fully_deleted_ids_truncated = _bounded_ids(
        fully_deleted_request_ids_all
    )
    return LotForcePurgeImpactPlan(
        lot_id=lot.id,
        lot_name=lot.name,
        lot_version=lot.version,
        confirmation_phrase=f"FORCE DELETE LOT {lot.id}",
        active_box_ids=sorted(active_box_ids_all),
        active_box_count=len(active_box_ids_all),
        active_box_ids_truncated=active_box_ids_truncated,
        archived_box_ids=sorted(archived_box_ids_all),
        archived_box_count=len(archived_box_ids_all),
        archived_box_ids_truncated=archived_box_ids_truncated,
        active_pallet_ids=sorted(active_pallet_ids_all),
        active_pallet_count=len(active_pallet_ids_all),
        active_pallet_ids_truncated=active_pallet_ids_truncated,
        archived_pallet_ids=sorted(archived_pallet_ids_all),
        archived_pallet_count=len(archived_pallet_ids_all),
        archived_pallet_ids_truncated=archived_pallet_ids_truncated,
        touched_request_ids=sorted(touched_request_ids_all),
        touched_request_count=len(touched_request_ids_all),
        touched_request_ids_truncated=touched_request_ids_truncated,
        request_rewrites=request_rewrites_all,
        request_rewrite_count=len(request_rewrites_all),
        request_rewrites_truncated=(
            len(request_rewrites_all) > _PURGE_LIST_LIMIT
        ),
        fully_deleted_request_ids=sorted(fully_deleted_request_ids_all),
        fully_deleted_request_count=len(fully_deleted_request_ids_all),
        fully_deleted_request_ids_truncated=fully_deleted_ids_truncated,
        incoming_lineage_detachments=lineage_detachments_all,
        incoming_lineage_detachment_count=len(lineage_detachments_all),
        incoming_lineage_detachments_truncated=(
            len(lineage_detachments_all) > _PURGE_LIST_LIMIT
        ),
        **file_counts,
        object_cleanup=object_cleanup,
        hard_blockers=hard_blockers,
        overridden_blockers=overridden_blockers,
        graph_signature=graph_signature,
        force_allowed=not hard_blockers,
    )


def _purge_object_keys(db: Session, request_ids: list[int]) -> list[str]:
    if not request_ids:
        return []
    keys = union_all(
        select(BoxRequestDocument.object_key.label("object_key")).where(
            BoxRequestDocument.request_id.in_(request_ids)
        ),
        select(BoxRequestAttachment.object_key.label("object_key")).where(
            BoxRequestAttachment.request_id.in_(request_ids)
        ),
        select(BoxRequestDiscrepancyPhoto.object_key.label("object_key"))
        .join(
            BoxRequestDiscrepancy,
            BoxRequestDiscrepancy.id
            == BoxRequestDiscrepancyPhoto.discrepancy_id,
        )
        .where(BoxRequestDiscrepancy.request_id.in_(request_ids)),
    ).subquery()
    return sorted(
        {
            str(key)
            for key in db.scalars(
                select(keys.c.object_key).where(keys.c.object_key.is_not(None))
            ).all()
        }
    )


def _purge_object_key_references(db: Session):
    return union_all(
        select(
            BoxRequestDocument.request_id.label("request_id"),
            BoxRequestDocument.object_key.label("object_key"),
        ),
        select(
            BoxRequestAttachment.request_id.label("request_id"),
            BoxRequestAttachment.object_key.label("object_key"),
        ),
        select(
            BoxRequestDiscrepancy.request_id.label("request_id"),
            BoxRequestDiscrepancyPhoto.object_key.label("object_key"),
        ).join(
            BoxRequestDiscrepancy,
            BoxRequestDiscrepancy.id
            == BoxRequestDiscrepancyPhoto.discrepancy_id,
        ),
    ).subquery()


def _purge_object_key_reference_count(db: Session, object_key: str) -> int:
    references = _purge_object_key_references(db)
    return int(
        db.scalar(
            select(func.count())
            .select_from(references)
            .where(references.c.object_key == object_key)
        )
        or 0
    )


def _purge_graph_signature(
    db: Session,
    *,
    lot: Lot,
    pallets: list[Pallet],
    boxes: list[Box],
    request_ids: list[int],
    blockers: list[LotPurgeBlocker],
) -> str:
    """Hash the complete operational graph used by preview and execution."""
    box_ids = [box.id for box in boxes]
    discrepancy_ids = (
        list(
            db.scalars(
                select(BoxRequestDiscrepancy.id)
                .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
                .order_by(BoxRequestDiscrepancy.id)
            ).all()
        )
        if request_ids
        else []
    )

    def rows(statement) -> list[list[object]]:
        return [list(row) for row in db.execute(statement).all()]

    graph: dict[str, object] = {
        "lot": [
            lot.id,
            lot.name,
            lot.version,
            lot.merged_into_lot_id,
            lot.merged_at,
        ],
        "boxes": [
            [
                box.id,
                box.lot_id,
                box.pallet_id,
                box.current_warehouse_id,
                _enum_text(box.status),
                box.archived_at,
            ]
            for box in boxes
        ],
        "files": (
            rows(
                select(
                    BoxFile.id,
                    BoxFile.lot_id,
                    BoxFile.box_id,
                    BoxFile.normalized_reference,
                    BoxFile.position,
                    BoxFile.archived_at,
                    BoxFile.version,
                )
                .where(BoxFile.box_id.in_(box_ids))
                .order_by(BoxFile.id)
            )
            if box_ids
            else []
        ),
        "file_events": (
            rows(
                select(
                    BoxFileEvent.id,
                    BoxFileEvent.file_id,
                    BoxFileEvent.event_type,
                    BoxFileEvent.occurred_at,
                )
                .where(
                    BoxFileEvent.file_id.in_(
                        select(BoxFile.id).where(
                            BoxFile.box_id.in_(box_ids)
                        )
                    )
                )
                .order_by(BoxFileEvent.id)
            )
            if box_ids
            else []
        ),
        "file_snapshot_links": (
            rows(
                select(
                    BoxRequestItemFileSnapshot.id,
                    BoxRequestItemFileSnapshot.request_item_id,
                    BoxRequestItemFileSnapshot.file_id,
                )
                .where(
                    BoxRequestItemFileSnapshot.file_id.in_(
                        select(BoxFile.id).where(
                            BoxFile.box_id.in_(box_ids)
                        )
                    )
                )
                .order_by(BoxRequestItemFileSnapshot.id)
            )
            if box_ids
            else []
        ),
        "pallets": [_pallet_signature_value(pallet) for pallet in pallets],
        "pallet_events": (
            rows(
                select(
                    PalletEvent.id,
                    PalletEvent.pallet_id,
                    PalletEvent.event_type,
                    PalletEvent.old_pallet_number,
                    PalletEvent.new_pallet_number,
                    PalletEvent.from_warehouse_id,
                    PalletEvent.to_warehouse_id,
                    PalletEvent.event_metadata,
                )
                .where(PalletEvent.pallet_id.in_([pallet.id for pallet in pallets]))
                .order_by(PalletEvent.id)
            )
            if pallets
            else []
        ),
        "requests": (
            rows(
                select(
                    BoxRequest.id,
                    BoxRequest.version,
                    BoxRequest.status,
                    BoxRequest.origin,
                    BoxRequest.direction,
                    BoxRequest.warehouse_id,
                    BoxRequest.source_inbound_request_id,
                    BoxRequest.parent_request_id,
                    BoxRequest.root_request_id,
                )
                .where(BoxRequest.id.in_(request_ids))
                .order_by(BoxRequest.id)
            )
            if request_ids
            else []
        ),
        "items": (
            rows(
                select(
                    BoxRequestItem.id,
                    BoxRequestItem.request_id,
                    BoxRequestItem.box_id,
                    BoxRequestItem.lot_id,
                    BoxRequestItem.pallet_id,
                    BoxRequestItem.pallet,
                )
                .where(BoxRequestItem.request_id.in_(request_ids))
                .order_by(BoxRequestItem.id)
            )
            if request_ids
            else []
        ),
        "request_events": (
            rows(
                select(
                    BoxRequestEvent.id,
                    BoxRequestEvent.request_id,
                    BoxRequestEvent.event_type,
                    BoxRequestEvent.from_status,
                    BoxRequestEvent.to_status,
                )
                .where(BoxRequestEvent.request_id.in_(request_ids))
                .order_by(BoxRequestEvent.id)
            )
            if request_ids
            else []
        ),
        "documents": (
            rows(
                select(
                    BoxRequestDocument.id,
                    BoxRequestDocument.request_id,
                    BoxRequestDocument.object_key,
                )
                .where(BoxRequestDocument.request_id.in_(request_ids))
                .order_by(BoxRequestDocument.id)
            )
            if request_ids
            else []
        ),
        "attachments": (
            rows(
                select(
                    BoxRequestAttachment.id,
                    BoxRequestAttachment.request_id,
                    BoxRequestAttachment.object_key,
                )
                .where(BoxRequestAttachment.request_id.in_(request_ids))
                .order_by(BoxRequestAttachment.id)
            )
            if request_ids
            else []
        ),
        "comments": (
            rows(
                select(BoxRequestComment.id, BoxRequestComment.request_id)
                .where(BoxRequestComment.request_id.in_(request_ids))
                .order_by(BoxRequestComment.id)
            )
            if request_ids
            else []
        ),
        "exceptions": (
            rows(
                select(BoxRequestException.id, BoxRequestException.request_id)
                .where(BoxRequestException.request_id.in_(request_ids))
                .order_by(BoxRequestException.id)
            )
            if request_ids
            else []
        ),
        "discrepancies": (
            rows(
                select(
                    BoxRequestDiscrepancy.id,
                    BoxRequestDiscrepancy.request_id,
                    BoxRequestDiscrepancy.request_item_id,
                    BoxRequestDiscrepancy.box_id,
                )
                .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
                .order_by(BoxRequestDiscrepancy.id)
            )
            if request_ids
            else []
        ),
        "discrepancy_photos": (
            rows(
                select(
                    BoxRequestDiscrepancyPhoto.id,
                    BoxRequestDiscrepancyPhoto.discrepancy_id,
                    BoxRequestDiscrepancyPhoto.object_key,
                )
                .where(
                    BoxRequestDiscrepancyPhoto.discrepancy_id.in_(discrepancy_ids)
                )
                .order_by(BoxRequestDiscrepancyPhoto.id)
            )
            if discrepancy_ids
            else []
        ),
        "notifications": (
            rows(
                select(InAppNotification.id, InAppNotification.request_id)
                .where(InAppNotification.request_id.in_(request_ids))
                .order_by(InAppNotification.id)
            )
            if request_ids
            else []
        ),
        "email_outbox": (
            rows(
                select(RequestEmailOutbox.id, RequestEmailOutbox.request_id)
                .where(RequestEmailOutbox.request_id.in_(request_ids))
                .order_by(RequestEmailOutbox.id)
            )
            if request_ids
            else []
        ),
        "box_events": (
            rows(
                select(BoxEvent.id, BoxEvent.box_id, BoxEvent.event_type)
                .where(BoxEvent.box_id.in_(box_ids))
                .order_by(BoxEvent.id)
            )
            if box_ids
            else []
        ),
        "lot_events": rows(
            select(LotEvent.id, LotEvent.event_type)
            .where(LotEvent.lot_id == lot.id)
            .order_by(LotEvent.id)
        ),
        "blockers": [
            [
                blocker.code,
                [[entity.entity_type, entity.entity_id] for entity in blocker.entities],
                blocker.entity_count,
            ]
            for blocker in blockers
        ],
    }
    encoded = json.dumps(graph, default=str, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _pallet_purge_snapshots(
    pallets: list[Pallet],
    boxes: list[Box],
) -> list[dict[str, object]]:
    """Snapshot pallet identity and topology for the durable purge ledger."""
    counts: dict[int, dict[str, int]] = {
        pallet.id: {"assigned": 0, "active": 0, "archived": 0}
        for pallet in pallets
    }
    warehouse_ids: dict[int, set[int]] = {pallet.id: set() for pallet in pallets}
    for box in boxes:
        if box.pallet_id not in counts:
            continue
        bucket = counts[box.pallet_id]
        bucket["assigned"] += 1
        bucket["archived" if box.archived_at is not None else "active"] += 1
        warehouse_ids[box.pallet_id].add(box.current_warehouse_id)
    return [
        {
            "id": pallet.id,
            "barcode": pallet.barcode,
            "pallet_number": pallet.pallet_number,
            "normalized_pallet_number": pallet.normalized_pallet_number,
            "version": pallet.version,
            "is_active": pallet.is_active,
            "archived_at": _collision_timestamp(pallet.archived_at),
            "archive_reason": pallet.archive_reason,
            "absorbed_into_pallet_id": pallet.absorbed_into_pallet_id,
            "absorbed_at": _collision_timestamp(pallet.absorbed_at),
            "assigned_box_count": counts[pallet.id]["assigned"],
            "active_box_count": counts[pallet.id]["active"],
            "archived_box_count": counts[pallet.id]["archived"],
            "warehouse_ids": sorted(warehouse_ids[pallet.id]),
        }
        for pallet in pallets
    ]


def analyze_lot_purge_eligibility(
    db: Session,
    *,
    lot_id: int,
    lock_for_update: bool = False,
) -> LotPurgeEligibility:
    """Analyze the only safely purgeable Lot shape without deleting anything.

    ``lock_for_update`` is the execution-time revalidation mode. It acquires
    deterministic exclusive Lot, Box, then request locks using explicit
    PostgreSQL ``OF`` targets; preview callers leave it false and remain
    read-only.
    """
    lot = db.scalar(
        _purge_lot_lock_statement(lot_id)
        if lock_for_update
        else select(Lot).where(Lot.id == lot_id)
    )
    if lot is None:
        raise LotPurgeNotFoundError(f"lot {lot_id} not found")

    pallets = list(
        db.scalars(
            _purge_pallet_lock_statement(lot_id)
            if lock_for_update
            else select(Pallet).where(Pallet.lot_id == lot_id).order_by(Pallet.id)
        ).all()
    )
    pallet_ids = [pallet.id for pallet in pallets]
    boxes = list(
        db.scalars(
            _purge_box_lock_statement(lot_id)
            if lock_for_update
            else select(Box).where(Box.lot_id == lot_id).order_by(Box.id)
        ).all()
    )
    box_ids = [box.id for box in boxes]
    file_counts = _purge_file_counts(
        db,
        box_ids=box_ids,
        lock_for_update=lock_for_update,
    )
    active_box_ids_all = [box.id for box in boxes if box.archived_at is None]
    archived_box_ids_all = [box.id for box in boxes if box.archived_at is not None]
    active_box_ids, active_truncated = _bounded_ids(active_box_ids_all)
    archived_box_ids, archived_truncated = _bounded_ids(archived_box_ids_all)

    linked_item = BoxRequestItem.lot_id == lot_id
    if box_ids:
        linked_item = or_(linked_item, BoxRequestItem.box_id.in_(box_ids))
    if pallet_ids:
        linked_item = or_(
            linked_item,
            BoxRequestItem.pallet_id.in_(pallet_ids),
        )
    request_ids = list(
        db.scalars(
            select(BoxRequestItem.request_id)
            .where(linked_item)
            .distinct()
            .order_by(BoxRequestItem.request_id)
        ).all()
    )
    if lock_for_update and request_ids:
        db.execute(_purge_request_lock_statement(request_ids)).all()
        db.execute(_purge_request_item_lock_statement(request_ids)).all()
        _lock_purge_owned_rows(
            db,
            lot_id=lot_id,
            box_ids=box_ids,
            request_ids=request_ids,
            pallet_ids=pallet_ids,
        )

    lot_item = BoxRequestItem.lot_id == lot_id
    if box_ids:
        lot_item = or_(lot_item, BoxRequestItem.box_id.in_(box_ids))
    request_rows = (
        db.execute(
            select(
                BoxRequest.id,
                BoxRequest.origin,
                BoxRequest.status,
                BoxRequest.direction,
                BoxRequest.source_inbound_request_id,
                BoxRequest.parent_request_id,
                BoxRequest.root_request_id,
                func.count(BoxRequestItem.id).label("item_count"),
                func.sum(case((lot_item, 1), else_=0)).label("lot_item_count"),
            )
            .join(BoxRequestItem, BoxRequestItem.request_id == BoxRequest.id)
            .where(BoxRequest.id.in_(request_ids))
            .group_by(
                BoxRequest.id,
                BoxRequest.origin,
                BoxRequest.status,
                BoxRequest.direction,
                BoxRequest.source_inbound_request_id,
                BoxRequest.parent_request_id,
                BoxRequest.root_request_id,
            )
            .order_by(BoxRequest.id)
        ).all()
        if request_ids
        else []
    )

    blockers: list[LotPurgeBlocker] = []

    def add_blocker(
        code: str,
        message: str,
        *,
        entity_type: str,
        entity_ids: list[int],
    ) -> None:
        entities = _purge_entities(entity_type, entity_ids)
        blockers.append(
            LotPurgeBlocker(
                code=code,
                message=message,
                remediation=message,
                entities=entities,
                entity_count=len(set(entity_ids)),
                entities_truncated=len(set(entity_ids)) > len(entities),
            )
        )

    if lot.merged_into_lot_id is not None:
        add_blocker(
            "merged_tombstone",
            "Merged source tombstones cannot be purged.",
            entity_type="lot",
            entity_ids=[lot.id, lot.merged_into_lot_id],
        )
    merged_source_ids = list(
        db.scalars(
            select(Lot.id)
            .where(Lot.merged_into_lot_id == lot.id)
            .order_by(Lot.id)
        ).all()
    )
    if merged_source_ids:
        add_blocker(
            "merge_target",
            "This lot is the target of one or more merged tombstones.",
            entity_type="lot",
            entity_ids=merged_source_ids,
        )
    if active_box_ids_all:
        add_blocker(
            "active_boxes",
            "Archive every box in the lot before purge.",
            entity_type="box",
            entity_ids=active_box_ids_all,
        )
    if not archived_box_ids_all:
        add_blocker(
            "no_archived_boxes",
            "A self-receipt purge requires at least one archived box.",
            entity_type="lot",
            entity_ids=[lot.id],
        )
    if not request_ids:
        add_blocker(
            "no_self_receipts",
            "No linked completed self-receipt was found for this lot.",
            entity_type="lot",
            entity_ids=[lot.id],
        )

    staged_ids: list[int] = []
    open_ids: list[int] = []
    terminal_ids: list[int] = []
    unsupported_origin_ids: list[int] = []
    unsupported_direction_ids: list[int] = []
    mixed_lot_ids: list[int] = []
    incomplete_provenance_ids: list[int] = []
    family_ids: list[int] = []
    request_previews: list[LotPurgeRequestPreview] = []
    incomplete_provenance_request_ids = (
        set(
            db.scalars(
                select(BoxRequestItem.request_id)
                .where(
                    BoxRequestItem.request_id.in_(request_ids),
                    BoxRequestItem.box_id.is_(None),
                )
                .distinct()
            ).all()
        )
        if request_ids
        else set()
    )
    for row in request_rows:
        request_id = int(row.id)
        item_count = int(row.item_count)
        lot_item_count = int(row.lot_item_count or 0)
        if len(request_previews) < _PURGE_LIST_LIMIT:
            request_previews.append(
                LotPurgeRequestPreview(
                    request_id=request_id,
                    origin=_enum_text(row.origin),
                    status=_enum_text(row.status),
                    direction=_enum_text(row.direction),
                    item_count=item_count,
                    lot_item_count=lot_item_count,
                )
            )
        if row.status in (BoxRequestStatus.draft, BoxRequestStatus.submitted):
            staged_ids.append(request_id)
        elif row.status != BoxRequestStatus.completed:
            if row.status in (
                BoxRequestStatus.approved,
                BoxRequestStatus.preparing,
                BoxRequestStatus.ready_for_transport,
                BoxRequestStatus.in_transit,
                BoxRequestStatus.awaiting_confirmation,
            ):
                open_ids.append(request_id)
            else:
                terminal_ids.append(request_id)
        if row.origin not in _PURGE_RECEIPT_ORIGINS:
            unsupported_origin_ids.append(request_id)
        if row.direction != BoxRequestDirection.inbound:
            unsupported_direction_ids.append(request_id)
        if lot_item_count != item_count:
            mixed_lot_ids.append(request_id)
        if request_id in incomplete_provenance_request_ids:
            incomplete_provenance_ids.append(request_id)
        if (
            row.source_inbound_request_id is not None
            or row.parent_request_id is not None
            or row.root_request_id != request_id
        ):
            family_ids.append(request_id)

    if staged_ids:
        add_blocker(
            "staged_requests",
            "Staged receipts must be resolved before purge.",
            entity_type="request",
            entity_ids=staged_ids,
        )
    if open_ids:
        add_blocker(
            "open_requests",
            "Open request workflow prevents purge.",
            entity_type="request",
            entity_ids=open_ids,
        )
    if terminal_ids:
        add_blocker(
            "requests_not_completed",
            "Every linked request must be completed.",
            entity_type="request",
            entity_ids=terminal_ids,
        )
    if unsupported_origin_ids:
        add_blocker(
            "unsupported_request_origin",
            "Only manual-entry and XLSX-import self-receipts may be purged.",
            entity_type="request",
            entity_ids=unsupported_origin_ids,
        )
    if unsupported_direction_ids:
        add_blocker(
            "unsupported_request_direction",
            "Return and other non-inbound requests prevent purge.",
            entity_type="request",
            entity_ids=unsupported_direction_ids,
        )
    if mixed_lot_ids:
        add_blocker(
            "mixed_lot_receipt",
            "A linked request contains items outside this lot.",
            entity_type="request",
            entity_ids=mixed_lot_ids,
        )
    if incomplete_provenance_ids:
        add_blocker(
            "incomplete_receipt_provenance",
            "Every self-receipt item must reference one archived physical box.",
            entity_type="request",
            entity_ids=incomplete_provenance_ids,
        )
    if family_ids:
        add_blocker(
            "request_family",
            "Follow-ups, returns, and request-family members prevent purge.",
            entity_type="request",
            entity_ids=family_ids,
        )

    incoming_reference_ids = (
        list(
            db.scalars(
                select(BoxRequest.id)
                .where(
                    or_(
                        BoxRequest.source_inbound_request_id.in_(request_ids),
                        BoxRequest.parent_request_id.in_(request_ids),
                        and_(
                            BoxRequest.root_request_id.in_(request_ids),
                            BoxRequest.id != BoxRequest.root_request_id,
                        ),
                    ),
                )
                .order_by(BoxRequest.id)
            ).all()
        )
        if request_ids
        else []
    )
    foreign_item_request_ids = (
        list(
            db.scalars(
                select(BoxRequestItem.request_id)
                .join(Box, Box.id == BoxRequestItem.box_id)
                .where(
                    linked_item,
                    BoxRequestItem.box_id.is_not(None),
                    or_(
                        BoxRequestItem.lot_id.is_distinct_from(lot_id),
                        Box.lot_id.is_distinct_from(lot_id),
                    ),
                )
                .distinct()
                .order_by(BoxRequestItem.request_id)
            ).all()
        )
        if request_ids
        else []
    )
    foreign_reference_ids = sorted(
        set(incoming_reference_ids) | set(foreign_item_request_ids)
    )
    if foreign_reference_ids:
        add_blocker(
            "foreign_request_reference",
            "A foreign request or inconsistent item points into this lot's receipts.",
            entity_type="request",
            entity_ids=foreign_reference_ids,
        )

    linked_box_ids = (
        set(
            db.scalars(
                select(BoxRequestItem.box_id)
                .where(
                    BoxRequestItem.request_id.in_(request_ids),
                    BoxRequestItem.box_id.in_(box_ids),
                )
                .distinct()
            ).all()
        )
        if request_ids and box_ids
        else set()
    )
    unlinked_archived_box_ids = sorted(set(archived_box_ids_all) - linked_box_ids)
    if unlinked_archived_box_ids:
        add_blocker(
            "unlinked_archived_boxes",
            "Every archived box must have completed self-receipt provenance.",
            entity_type="box",
            entity_ids=unlinked_archived_box_ids,
        )

    event_rows = (
        db.execute(
            select(
                BoxRequestEvent.request_id,
                func.count(BoxRequestEvent.id).label("event_count"),
                func.sum(
                    case(
                        (
                            or_(
                                BoxRequestEvent.event_type
                                != BoxRequestEventType.completed,
                                BoxRequestEvent.to_status.is_distinct_from(
                                    BoxRequestStatus.completed
                                ),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("unsupported_count"),
            )
            .where(BoxRequestEvent.request_id.in_(request_ids))
            .group_by(BoxRequestEvent.request_id)
        ).all()
        if request_ids
        else []
    )
    event_shape = {
        int(row.request_id): (int(row.event_count), int(row.unsupported_count or 0))
        for row in event_rows
    }
    request_history_ids = [
        request_id
        for request_id in request_ids
        if event_shape.get(request_id) != (1, 0)
    ]
    if request_history_ids:
        add_blocker(
            "workflow_history",
            "Only the initial completed self-receipt event is allowed.",
            entity_type="request",
            entity_ids=request_history_ids,
        )

    lot_history_rows = db.execute(
        select(LotEvent.event_type, LotEvent.id)
        .where(
            LotEvent.lot_id == lot.id,
            LotEvent.event_type.in_(
                (LotEventType.reassigned, LotEventType.merged)
            ),
        )
        .order_by(LotEvent.id)
    ).all()
    reassigned_event_ids = [
        int(event_id)
        for event_type, event_id in lot_history_rows
        if event_type == LotEventType.reassigned
    ]
    merged_event_ids = [
        int(event_id)
        for event_type, event_id in lot_history_rows
        if event_type == LotEventType.merged
    ]
    box_reassignment_event_ids = (
        list(
            db.scalars(
                select(BoxEvent.id)
                .where(
                    BoxEvent.box_id.in_(box_ids),
                    BoxEvent.event_type == BoxEventType.lot_reassigned,
                )
                .order_by(BoxEvent.id)
            ).all()
        )
        if box_ids
        else []
    )
    if reassigned_event_ids or box_reassignment_event_ids:
        add_blocker(
            "box_reassignment_history",
            "Per-box lot reassignment history prevents purge.",
            entity_type="event",
            entity_ids=reassigned_event_ids + box_reassignment_event_ids,
        )
    if merged_event_ids:
        add_blocker(
            "merge_history",
            "Lot merge history prevents purge.",
            entity_type="event",
            entity_ids=merged_event_ids,
        )

    noninitial_box_event_ids = (
        list(
            db.scalars(
                select(BoxEvent.id)
                .where(
                    BoxEvent.box_id.in_(box_ids),
                    BoxEvent.event_type.not_in(
                        (
                            BoxEventType.created,
                            BoxEventType.archived,
                            BoxEventType.lot_reassigned,
                            BoxEventType.pallet_assigned,
                        )
                    ),
                )
                .order_by(BoxEvent.id)
            ).all()
        )
        if box_ids
        else []
    )
    if noninitial_box_event_ids:
        add_blocker(
            "box_workflow_history",
            "Box movement, restoration, return, or status history prevents purge.",
            entity_type="event",
            entity_ids=noninitial_box_event_ids,
        )

    linked_request_ids, request_ids_truncated = _bounded_ids(request_ids)
    object_keys = _purge_object_keys(db, request_ids)
    if object_keys:
        references = _purge_object_key_references(db)
        shared_request_ids = list(
            db.scalars(
                select(references.c.request_id)
                .where(
                    references.c.object_key.in_(object_keys),
                    references.c.request_id.not_in(request_ids),
                )
                .distinct()
                .order_by(references.c.request_id)
            ).all()
        )
        if shared_request_ids:
            add_blocker(
                "shared_object_key",
                "Stored objects referenced by another request cannot be purged.",
                entity_type="request",
                entity_ids=shared_request_ids,
            )
    graph_signature = _purge_graph_signature(
        db,
        lot=lot,
        pallets=pallets,
        boxes=boxes,
        request_ids=request_ids,
        blockers=blockers,
    )
    return LotPurgeEligibility(
        lot_id=lot.id,
        lot_name=lot.name,
        lot_version=lot.version,
        active_box_count=len(active_box_ids_all),
        active_box_ids=active_box_ids,
        active_box_ids_truncated=active_truncated,
        archived_box_count=len(archived_box_ids_all),
        archived_box_ids=archived_box_ids,
        archived_box_ids_truncated=archived_truncated,
        active_pallet_count=sum(pallet.is_active for pallet in pallets),
        active_pallet_ids=[
            pallet.id for pallet in pallets if pallet.is_active
        ],
        active_pallet_ids_truncated=(
            sum(pallet.is_active for pallet in pallets) > _PURGE_LIST_LIMIT
        ),
        archived_pallet_count=sum(not pallet.is_active for pallet in pallets),
        archived_pallet_ids=[
            pallet.id for pallet in pallets if not pallet.is_active
        ],
        archived_pallet_ids_truncated=(
            sum(not pallet.is_active for pallet in pallets) > _PURGE_LIST_LIMIT
        ),
        linked_request_count=len(request_ids),
        linked_request_ids=linked_request_ids,
        linked_request_ids_truncated=request_ids_truncated,
        requests=request_previews,
        requests_truncated=len(request_rows) > len(request_previews),
        object_key_count=len(object_keys),
        **file_counts,
        graph_signature=graph_signature,
        eligible=not blockers,
        blockers=blockers,
    )


def _delete_count(db: Session, statement) -> int:
    result = db.execute(statement.execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def _retire_lot_graph_barcodes(
    db: Session,
    *,
    user: User,
    reason: str,
    operation: str,
    lot: Lot | None = None,
    pallets: list[Pallet] | None = None,
    boxes: list[Box] | None = None,
    files: list[BoxFile] | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """Retire a locked destructive graph in one registry query."""
    pallets = pallets or []
    boxes = boxes or []
    files = files or []
    lot_names = {lot.id: lot.name} if lot is not None else {}
    box_by_id = {box.id: box for box in boxes}
    targets = []
    if lot is not None:
        targets.append(
            barcode_retirement_target(
                lot,
                hierarchy={
                    "lot_id": lot.id,
                    "lot_barcode": lot.barcode,
                    "lot_name": lot.name,
                },
            )
        )
    for pallet in pallets:
        targets.append(
            barcode_retirement_target(
                pallet,
                hierarchy={
                    "lot_id": pallet.lot_id,
                    "lot_barcode": pallet.lot.barcode,
                    "lot_name": lot_names.get(pallet.lot_id),
                    "pallet_id": pallet.id,
                    "pallet_barcode": pallet.barcode,
                    "pallet_number": pallet.pallet_number,
                },
            )
        )
    for box in boxes:
        targets.append(
            barcode_retirement_target(
                box,
                hierarchy={
                    "lot_id": box.lot_id,
                    "lot_barcode": box.lot_record.barcode,
                    "lot_name": lot_names.get(box.lot_id),
                    "box_id": box.id,
                    "box_barcode": box.barcode,
                    "box_number": box.box_number,
                    "pallet_id": box.pallet_id,
                    "pallet_barcode": (
                        box.pallet.barcode if box.pallet is not None else None
                    ),
                    "warehouse_id": box.current_warehouse_id,
                },
            )
        )
    for file in files:
        parent = box_by_id.get(file.box_id)
        targets.append(
            barcode_retirement_target(
                file,
                hierarchy={
                    "lot_id": file.lot_id,
                    "lot_barcode": file.lot.barcode,
                    "lot_name": lot_names.get(file.lot_id),
                    "box_id": file.box_id,
                    "box_barcode": parent.barcode if parent is not None else None,
                    "box_number": parent.box_number if parent is not None else None,
                    "file_id": file.id,
                    "file_barcode": file.barcode,
                    "file_reference": file.reference,
                },
            )
        )
    retire_barcode_identities(
        db,
        targets,
        actor_user_id=user.id,
        reason=reason,
        operation_metadata={"operation": operation, **(metadata or {})},
    )


def _delete_file_graph_for_boxes(
    db: Session,
    *,
    box_ids: list[int],
) -> dict[str, int]:
    """Detach immutable snapshot links, then delete File history in FK order."""
    if not box_ids:
        return {
            "box_request_item_file_snapshot_links_detached": 0,
            "box_file_events": 0,
            "box_files": 0,
        }
    file_ids = list(
        db.scalars(
            select(BoxFile.id)
            .where(BoxFile.box_id.in_(box_ids))
            .order_by(BoxFile.id)
        ).all()
    )
    if not file_ids:
        return {
            "box_request_item_file_snapshot_links_detached": 0,
            "box_file_events": 0,
            "box_files": 0,
        }
    detached = db.execute(
        update(BoxRequestItemFileSnapshot)
        .where(BoxRequestItemFileSnapshot.file_id.in_(file_ids))
        .values(file_id=None)
        .execution_options(synchronize_session=False)
    )
    return {
        "box_request_item_file_snapshot_links_detached": int(
            detached.rowcount or 0
        ),
        "box_file_events": _delete_count(
            db,
            delete(BoxFileEvent).where(BoxFileEvent.file_id.in_(file_ids)),
        ),
        "box_files": _delete_count(
            db,
            delete(BoxFile).where(BoxFile.id.in_(file_ids)),
        ),
    }


def _delete_purge_graph(
    db: Session,
    *,
    lot_id: int,
    box_ids: list[int],
    pallet_ids: list[int],
    request_ids: list[int],
    expected_version: int,
    locked: LotPurgeEligibility,
) -> dict[str, int]:
    """Delete one already-locked eligible graph in strict FK order."""
    deleted_counts: dict[str, int] = {}
    discrepancy_ids = (
        list(
            db.scalars(
                select(BoxRequestDiscrepancy.id)
                .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
                .order_by(BoxRequestDiscrepancy.id)
            ).all()
        )
        if request_ids
        else []
    )
    if discrepancy_ids:
        deleted_counts["box_request_discrepancy_photos"] = _delete_count(
            db,
            delete(BoxRequestDiscrepancyPhoto).where(
                BoxRequestDiscrepancyPhoto.discrepancy_id.in_(discrepancy_ids)
            ),
        )
    if request_ids:
        for name, model in (
            ("box_request_discrepancies", BoxRequestDiscrepancy),
            ("in_app_notifications", InAppNotification),
            ("request_email_outbox", RequestEmailOutbox),
            ("box_request_documents", BoxRequestDocument),
            ("box_request_attachments", BoxRequestAttachment),
            ("box_request_comments", BoxRequestComment),
            ("box_request_exceptions", BoxRequestException),
            ("box_request_events", BoxRequestEvent),
        ):
            deleted_counts[name] = _delete_count(
                db,
                delete(model).where(model.request_id.in_(request_ids)),
            )
        deleted_counts["box_request_items"] = _delete_count(
            db,
            delete(BoxRequestItem).where(BoxRequestItem.request_id.in_(request_ids)),
        )
        # Eligible requests are roots that self-reference through root_request_id.
        # Clear every self-FK explicitly before deleting the roots; incoming
        # references were already rejected by the locked analyzer.
        db.execute(
            update(BoxRequest)
            .where(BoxRequest.id.in_(request_ids))
            .values(
                source_inbound_request_id=None,
                parent_request_id=None,
                root_request_id=None,
            )
            .execution_options(synchronize_session=False)
        )
        deleted_counts["box_requests"] = _delete_count(
            db,
            delete(BoxRequest).where(BoxRequest.id.in_(request_ids)),
        )
    if box_ids:
        deleted_counts.update(
            _delete_file_graph_for_boxes(db, box_ids=box_ids)
        )
        deleted_counts["box_events"] = _delete_count(
            db,
            delete(BoxEvent).where(BoxEvent.box_id.in_(box_ids)),
        )
        deleted_counts["boxes"] = _delete_count(
            db,
            delete(Box).where(Box.id.in_(box_ids)),
        )
    if pallet_ids:
        deleted_counts["pallet_events"] = _delete_count(
            db,
            delete(PalletEvent).where(PalletEvent.pallet_id.in_(pallet_ids)),
        )
        deleted_counts["pallets"] = _delete_count(
            db,
            delete(Pallet).where(
                Pallet.id.in_(pallet_ids),
                Pallet.lot_id == lot_id,
            ),
        )
    deleted_counts["lot_events"] = _delete_count(
        db,
        delete(LotEvent).where(LotEvent.lot_id == lot_id),
    )
    deleted_counts["lots"] = _delete_count(
        db,
        delete(Lot).where(Lot.id == lot_id, Lot.version == expected_version),
    )
    if deleted_counts["lots"] != 1:
        raise LotPurgeConflictError(
            "the locked lot changed before deletion",
            code="graph_changed",
            preview=locked,
        )
    return deleted_counts


def _force_purge_conflict(
    message: str,
    *,
    code: str,
    preview: LotForcePurgeImpactPlan,
) -> None:
    raise LotPurgeConflictError(message, code=code, preview=preview)


def _delete_force_request_graph(
    db: Session,
    *,
    request_ids: list[int],
) -> dict[str, int]:
    deleted_counts: dict[str, int] = {}
    if not request_ids:
        return deleted_counts
    discrepancy_ids = list(
        db.scalars(
            select(BoxRequestDiscrepancy.id)
            .where(BoxRequestDiscrepancy.request_id.in_(request_ids))
            .order_by(BoxRequestDiscrepancy.id)
        ).all()
    )
    if discrepancy_ids:
        deleted_counts["box_request_discrepancy_photos"] = _delete_count(
            db,
            delete(BoxRequestDiscrepancyPhoto).where(
                BoxRequestDiscrepancyPhoto.discrepancy_id.in_(discrepancy_ids)
            ),
        )
    for name, model in (
        ("box_request_discrepancies", BoxRequestDiscrepancy),
        ("in_app_notifications", InAppNotification),
        ("request_email_outbox", RequestEmailOutbox),
        ("box_request_documents", BoxRequestDocument),
        ("box_request_attachments", BoxRequestAttachment),
        ("box_request_comments", BoxRequestComment),
        ("box_request_exceptions", BoxRequestException),
        ("box_request_events", BoxRequestEvent),
    ):
        deleted_counts[name] = _delete_count(
            db,
            delete(model).where(model.request_id.in_(request_ids)),
        )
    deleted_counts["box_request_items"] = _delete_count(
        db,
        delete(BoxRequestItem).where(BoxRequestItem.request_id.in_(request_ids)),
    )
    db.execute(
        update(BoxRequest)
        .where(BoxRequest.id.in_(request_ids))
        .values(
            source_inbound_request_id=None,
            parent_request_id=None,
            root_request_id=None,
        )
        .execution_options(synchronize_session=False)
    )
    deleted_counts["box_requests"] = _delete_count(
        db,
        delete(BoxRequest).where(BoxRequest.id.in_(request_ids)),
    )
    return deleted_counts


def _force_deleted_entity_ids(
    db: Session,
    *,
    plan: LotForcePurgeImpactPlan,
    box_ids: list[int],
) -> dict[str, list[int]]:
    """Snapshot every row ID force purge will remove before mutation."""
    request_ids = plan.fully_deleted_request_ids

    def request_owned_ids(model) -> list[int]:
        if not request_ids:
            return []
        return list(
            db.scalars(
                select(model.id)
                .where(model.request_id.in_(request_ids))
                .order_by(model.id)
            ).all()
        )

    full_item_ids = request_owned_ids(BoxRequestItem)
    full_discrepancy_ids = request_owned_ids(BoxRequestDiscrepancy)
    removed_item_ids = sorted(
        {
            item_id
            for rewrite in plan.request_rewrites
            for item_id in rewrite.removed_item_ids
        }
    )
    removed_discrepancy_ids = sorted(
        {
            discrepancy_id
            for rewrite in plan.request_rewrites
            for discrepancy_id in rewrite.removed_discrepancy_ids
        }
    )
    deleted_discrepancy_ids = sorted(
        set(full_discrepancy_ids) | set(removed_discrepancy_ids)
    )
    deleted_photo_ids = (
        list(
            db.scalars(
                select(BoxRequestDiscrepancyPhoto.id)
                .where(
                    BoxRequestDiscrepancyPhoto.discrepancy_id.in_(
                        deleted_discrepancy_ids
                    )
                )
                .order_by(BoxRequestDiscrepancyPhoto.id)
            ).all()
        )
        if deleted_discrepancy_ids
        else []
    )
    file_ids = (
        list(
            db.scalars(
                select(BoxFile.id)
                .where(BoxFile.box_id.in_(box_ids))
                .order_by(BoxFile.id)
            ).all()
        )
        if box_ids
        else []
    )
    return {
        "lots": [plan.lot_id],
        "boxes": sorted(box_ids),
        "pallets": sorted(
            plan.active_pallet_ids + plan.archived_pallet_ids
        ),
        "pallet_events": (
            list(
                db.scalars(
                    select(PalletEvent.id)
                    .where(
                        PalletEvent.pallet_id.in_(
                            plan.active_pallet_ids + plan.archived_pallet_ids
                        )
                    )
                    .order_by(PalletEvent.id)
                ).all()
            )
            if plan.active_pallet_ids or plan.archived_pallet_ids
            else []
        ),
        "box_events": (
            list(
                db.scalars(
                    select(BoxEvent.id)
                    .where(BoxEvent.box_id.in_(box_ids))
                    .order_by(BoxEvent.id)
                ).all()
            )
            if box_ids
            else []
        ),
        "box_files": file_ids,
        "box_file_events": (
            list(
                db.scalars(
                    select(BoxFileEvent.id)
                    .where(BoxFileEvent.file_id.in_(file_ids))
                    .order_by(BoxFileEvent.id)
                ).all()
            )
            if file_ids
            else []
        ),
        "detached_request_file_snapshot_ids": (
            list(
                db.scalars(
                    select(BoxRequestItemFileSnapshot.id)
                    .where(
                        BoxRequestItemFileSnapshot.file_id.in_(file_ids)
                    )
                    .order_by(BoxRequestItemFileSnapshot.id)
                ).all()
            )
            if file_ids
            else []
        ),
        "lot_events": list(
            db.scalars(
                select(LotEvent.id)
                .where(LotEvent.lot_id == plan.lot_id)
                .order_by(LotEvent.id)
            ).all()
        ),
        "box_requests": sorted(request_ids),
        "box_request_items": sorted(set(full_item_ids) | set(removed_item_ids)),
        "box_request_discrepancies": deleted_discrepancy_ids,
        "box_request_discrepancy_photos": deleted_photo_ids,
        "box_request_events": request_owned_ids(BoxRequestEvent),
        "box_request_exceptions": request_owned_ids(BoxRequestException),
        "box_request_documents": request_owned_ids(BoxRequestDocument),
        "box_request_comments": request_owned_ids(BoxRequestComment),
        "box_request_attachments": request_owned_ids(BoxRequestAttachment),
        "in_app_notifications": request_owned_ids(InAppNotification),
        "request_email_outbox": request_owned_ids(RequestEmailOutbox),
    }


def _apply_force_request_adjustments(
    db: Session,
    *,
    user: User,
    audit_id: int,
    plan: LotForcePurgeImpactPlan,
) -> dict[str, int]:
    deleted_counts: dict[str, int] = {}
    rewrites = {rewrite.request_id: rewrite for rewrite in plan.request_rewrites}
    detachments_by_request: dict[int, list[LotForcePurgeLineageDetach]] = {}
    for detachment in plan.incoming_lineage_detachments:
        detachments_by_request.setdefault(detachment.request_id, []).append(detachment)
    adjusted_request_ids = sorted(set(rewrites) | set(detachments_by_request))
    request_state = {
        int(row.id): (int(row.version), row.status)
        for row in db.execute(
            select(BoxRequest.id, BoxRequest.version, BoxRequest.status).where(
                BoxRequest.id.in_(adjusted_request_ids)
            )
        ).all()
    }
    now = datetime.now(UTC)

    for request_id in adjusted_request_ids:
        rewrite = rewrites.get(request_id)
        detachments = detachments_by_request.get(request_id, [])
        if request_id not in request_state:
            _force_purge_conflict(
                "a preserved request disappeared during force purge",
                code="graph_changed",
                preview=plan,
            )
        before_version, request_status = request_state[request_id]
        values: dict[str, object] = {
            "version": before_version + 1,
            "updated_at": now,
        }
        metadata: dict[str, object] = {
            "operation": "lot_force_purge_adjustment",
            "purge_audit_id": audit_id,
            "lot_id": plan.lot_id,
            "before_version": before_version,
            "after_version": before_version + 1,
        }

        if rewrite is not None:
            if rewrite.removed_discrepancy_photo_ids:
                deleted_counts["box_request_discrepancy_photos"] = (
                    deleted_counts.get("box_request_discrepancy_photos", 0)
                    + _delete_count(
                        db,
                        delete(BoxRequestDiscrepancyPhoto).where(
                            BoxRequestDiscrepancyPhoto.id.in_(
                                rewrite.removed_discrepancy_photo_ids
                            )
                        ),
                    )
                )
            if rewrite.removed_discrepancy_ids:
                deleted_counts["box_request_discrepancies"] = (
                    deleted_counts.get("box_request_discrepancies", 0)
                    + _delete_count(
                        db,
                        delete(BoxRequestDiscrepancy).where(
                            BoxRequestDiscrepancy.id.in_(
                                rewrite.removed_discrepancy_ids
                            )
                        ),
                    )
                )
            if rewrite.removed_item_ids:
                deleted_counts["box_request_items"] = (
                    deleted_counts.get("box_request_items", 0)
                    + _delete_count(
                        db,
                        delete(BoxRequestItem).where(
                            BoxRequestItem.id.in_(rewrite.removed_item_ids)
                        ),
                    )
                )
                surviving_positions = [
                    position
                    for position in rewrite.item_positions
                    if position.after_position is not None
                ]
                if surviving_positions:
                    offset = (
                        max(position.before_position for position in rewrite.item_positions)
                        + len(surviving_positions)
                        + 1
                    )
                    db.execute(
                        update(BoxRequestItem)
                        .where(
                            BoxRequestItem.id.in_(
                                [position.item_id for position in surviving_positions]
                            )
                        )
                        .values(position=BoxRequestItem.position + offset)
                        .execution_options(synchronize_session=False)
                    )
                    for position in surviving_positions:
                        db.execute(
                            update(BoxRequestItem)
                            .where(BoxRequestItem.id == position.item_id)
                            .values(position=position.after_position)
                            .execution_options(synchronize_session=False)
                        )
            values.update(
                {
                    "quantity": rewrite.after_quantity,
                    "actual_received_quantity": (
                        rewrite.after_actual_received_quantity
                    ),
                    "variance_quantity": rewrite.after_variance_quantity,
                }
            )
            metadata["rewrite"] = {
                "before": {
                    "item_count": rewrite.before_item_count,
                    "quantity": rewrite.before_quantity,
                    "actual_received_quantity": (
                        rewrite.before_actual_received_quantity
                    ),
                    "variance_quantity": rewrite.before_variance_quantity,
                },
                "after": {
                    "item_count": rewrite.after_item_count,
                    "quantity": rewrite.after_quantity,
                    "actual_received_quantity": (
                        rewrite.after_actual_received_quantity
                    ),
                    "variance_quantity": rewrite.after_variance_quantity,
                },
                "removed_item_ids": rewrite.removed_item_ids,
                "removed_discrepancy_ids": rewrite.removed_discrepancy_ids,
                "removed_discrepancy_photo_ids": (
                    rewrite.removed_discrepancy_photo_ids
                ),
                "positions": [
                    {
                        "item_id": position.item_id,
                        "before": position.before_position,
                        "after": position.after_position,
                    }
                    for position in rewrite.item_positions
                ],
                "preserved_sibling_lot_ids": rewrite.preserved_sibling_lot_ids,
            }
        if detachments:
            detached_fields: list[dict[str, object]] = []
            for detachment in detachments:
                values[detachment.field_name] = None
                detached_fields.append(
                    {
                        "field": detachment.field_name,
                        "before": (
                            detachment.deleted_target_request_id
                        ),
                        "after": None,
                    }
                )
            metadata["lineage_detachments"] = detached_fields

        result = db.execute(
            update(BoxRequest)
            .where(
                BoxRequest.id == request_id,
                BoxRequest.version == before_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            _force_purge_conflict(
                "a preserved request changed during force purge",
                code="graph_changed",
                preview=plan,
            )
        db.add(
            BoxRequestEvent(
                request_id=request_id,
                event_type=BoxRequestEventType.force_purge_adjusted,
                from_status=request_status,
                to_status=request_status,
                user_id=user.id,
                note="Request adjusted by administrative Lot force purge.",
                occurred_at=now,
                event_metadata=metadata,
            )
        )
    return deleted_counts


def force_purge_lot(
    db: Session,
    *,
    user: User,
    lot_id: int,
    confirmation_name: str,
    confirmation_phrase: str,
    reason: str,
    expected_version: int,
    expected_graph_signature: str,
    acknowledged_blocker_codes: list[str],
) -> LotForcePurgeResult:
    """Atomically execute a locked surgical force purge and durable audit."""
    if user.role != UserRole.admin:
        raise LotAccessError("lot force purge requires admin role")
    cleaned_reason = reason.strip()
    if len(cleaned_reason) < 20:
        raise LotRuleError("force purge reason must be at least 20 characters")
    if len(cleaned_reason) > 2000:
        raise LotRuleError("force purge reason must be at most 2000 characters")

    prelock = analyze_lot_force_purge_impact(db, lot_id=lot_id)
    locked = analyze_lot_force_purge_impact(
        db,
        lot_id=lot_id,
        lock_for_update=True,
    )
    if locked.lot_version != expected_version:
        _force_purge_conflict(
            (
                "lot version conflict: expected "
                f"{expected_version}, current {locked.lot_version}"
            ),
            code="version_conflict",
            preview=locked,
        )
    if confirmation_name != locked.lot_name:
        _force_purge_conflict(
            "confirmation name must exactly match the current lot name",
            code="confirmation_name_mismatch",
            preview=locked,
        )
    if confirmation_phrase != locked.confirmation_phrase:
        _force_purge_conflict(
            "confirmation phrase must exactly match the server phrase",
            code="confirmation_phrase_mismatch",
            preview=locked,
        )
    if (
        expected_graph_signature != locked.graph_signature
        or prelock.graph_signature != locked.graph_signature
    ):
        _force_purge_conflict(
            "the lot graph changed after preview; review the latest preview",
            code="graph_changed",
            preview=locked,
        )
    if locked.hard_blockers:
        _force_purge_conflict(
            "lot force purge is blocked by a non-overridable safeguard",
            code="force_purge_blocked",
            preview=locked,
        )
    required_codes = {blocker.code for blocker in locked.overridden_blockers}
    supplied_codes = set(acknowledged_blocker_codes)
    if (
        supplied_codes != required_codes
        or len(acknowledged_blocker_codes) != len(supplied_codes)
    ):
        _force_purge_conflict(
            "every current overridden safeguard must be acknowledged exactly once",
            code="acknowledgement_mismatch",
            preview=locked,
        )

    lot = db.get(Lot, lot_id)
    assert lot is not None
    pallets = list(
        db.scalars(
            select(Pallet).where(Pallet.lot_id == lot_id).order_by(Pallet.id)
        ).all()
    )
    pallet_ids = [pallet.id for pallet in pallets]
    boxes = list(
        db.scalars(select(Box).where(Box.lot_id == lot_id).order_by(Box.id)).all()
    )
    pallet_snapshots = _pallet_purge_snapshots(pallets, boxes)
    all_box_ids = [box.id for box in boxes]
    request_rows = list(
        db.scalars(
            select(BoxRequest)
            .where(BoxRequest.id.in_(locked.touched_request_ids))
            .order_by(BoxRequest.id)
        ).all()
    )
    warehouse_ids = sorted(
        {box.current_warehouse_id for box in boxes}
        | {request.warehouse_id for request in request_rows}
    )
    object_keys = list(locked.object_cleanup.deletable_keys)
    deleted_entity_ids = _force_deleted_entity_ids(
        db,
        plan=locked,
        box_ids=all_box_ids,
    )
    force_purge_files = (
        list(
            db.scalars(
                select(BoxFile)
                .where(BoxFile.id.in_(deleted_entity_ids["box_files"]))
                .order_by(BoxFile.id)
            ).all()
        )
        if deleted_entity_ids["box_files"]
        else []
    )
    force_file_snapshots = [
        {
            "id": file.id,
            "lot_id": file.lot_id,
            "lot_barcode": file.lot.barcode,
            "box_id": file.box_id,
            "box_barcode": file.box.barcode,
            "pallet_id": file.box.pallet_id,
            "pallet_barcode": (
                file.box.pallet.barcode if file.box.pallet is not None else None
            ),
            "reference": file.reference,
            "normalized_reference": file.normalized_reference,
            "description": file.description,
            "barcode": file.barcode,
            "position": file.position,
            "archived_at": (
                file.archived_at.isoformat()
                if file.archived_at is not None
                else None
            ),
        }
        for file in force_purge_files
    ]
    force_box_snapshots = [
        {
            "id": box.id,
            "barcode": box.barcode,
            "box_number": box.box_number,
            "lot_id": box.lot_id,
            "lot_barcode": lot.barcode,
            "pallet_id": box.pallet_id,
            "pallet_barcode": (
                box.pallet.barcode if box.pallet is not None else None
            ),
            "warehouse_id": box.current_warehouse_id,
            "status": _enum_text(box.status),
            "archived_at": _collision_timestamp(box.archived_at),
        }
        for box in boxes
    ]
    cleanup_status = (
        LotPurgeCleanupStatus.pending
        if object_keys
        else LotPurgeCleanupStatus.not_required
    )
    audit = LotPurgeEvent(
        lot_id=lot.id,
        lot_name=lot.name,
        lot_version=lot.version,
        actor_user_id=user.id,
        reason=cleaned_reason,
        receipt_ids=list(locked.touched_request_ids),
        receipt_count=locked.touched_request_count,
        archived_box_ids=all_box_ids,
        archived_box_count=len(all_box_ids),
        pallet_ids=pallet_ids,
        pallet_count=len(pallet_ids),
        pallet_snapshots=pallet_snapshots,
        object_keys=object_keys,
        object_key_count=len(object_keys),
        object_cleanup_status=cleanup_status,
        object_cleanup_completed_at=(
            datetime.now(UTC)
            if cleanup_status == LotPurgeCleanupStatus.not_required
            else None
        ),
        event_metadata={
            "operation": "lot_force_purge",
            "purge_mode": "force",
            "confirmation_policy": "exact_case_sensitive_no_normalization",
            "confirmation_phrase": locked.confirmation_phrase,
            "graph_signature": locked.graph_signature,
            "acknowledged_blocker_codes": sorted(supplied_codes),
            "overridden_blocker_codes": sorted(required_codes),
            "warehouse_ids": warehouse_ids,
            "lot_barcode": lot.barcode,
            "active_box_ids": locked.active_box_ids,
            "archived_box_ids": locked.archived_box_ids,
            "box_snapshots": force_box_snapshots,
            "file_count": len(force_purge_files),
            "active_file_count": sum(
                file.archived_at is None for file in force_purge_files
            ),
            "archived_file_count": sum(
                file.archived_at is not None for file in force_purge_files
            ),
            "file_event_count": len(
                deleted_entity_ids["box_file_events"]
            ),
            "file_snapshots": force_file_snapshots,
            "file_cleanup_plan": {
                "detach_request_snapshot_links_with_set_null": True,
                "detached_request_snapshot_ids": deleted_entity_ids[
                    "detached_request_file_snapshot_ids"
                ],
                "delete_file_events_first": True,
                "hard_delete_file_ids": deleted_entity_ids["box_files"],
            },
            "pallet_ids": pallet_ids,
            "pallet_count": len(pallet_ids),
            "pallet_snapshots": pallet_snapshots,
            "pallet_cleanup_plan": {
                "null_preserved_request_item_pallet_ids": True,
                "delete_pallet_event_rows_first": True,
                "hard_delete_pallet_ids": pallet_ids,
                "scope_lot_id": lot_id,
            },
            "rewritten_request_ids": [
                rewrite.request_id for rewrite in locked.request_rewrites
            ],
            "request_rewrites": [
                {
                    "request_id": rewrite.request_id,
                    "origin": rewrite.origin,
                    "status": rewrite.status,
                    "direction": rewrite.direction,
                    "before": {
                        "item_count": rewrite.before_item_count,
                        "quantity": rewrite.before_quantity,
                        "actual_received_quantity": (
                            rewrite.before_actual_received_quantity
                        ),
                        "variance_quantity": rewrite.before_variance_quantity,
                    },
                    "after": {
                        "item_count": rewrite.after_item_count,
                        "quantity": rewrite.after_quantity,
                        "actual_received_quantity": (
                            rewrite.after_actual_received_quantity
                        ),
                        "variance_quantity": rewrite.after_variance_quantity,
                    },
                    "positions": [
                        {
                            "item_id": position.item_id,
                            "before": position.before_position,
                            "after": position.after_position,
                        }
                        for position in rewrite.item_positions
                    ],
                    "removed_item_ids": rewrite.removed_item_ids,
                    "removed_discrepancy_ids": rewrite.removed_discrepancy_ids,
                    "removed_discrepancy_photo_ids": (
                        rewrite.removed_discrepancy_photo_ids
                    ),
                    "preserved_sibling_lot_ids": (
                        rewrite.preserved_sibling_lot_ids
                    ),
                }
                for rewrite in locked.request_rewrites
            ],
            "deleted_request_ids": locked.fully_deleted_request_ids,
            "deleted_entity_ids": deleted_entity_ids,
            "lineage_detachments": [
                {
                    "request_id": detachment.request_id,
                    "field": detachment.field_name,
                    "deleted_request_id": detachment.deleted_target_request_id,
                }
                for detachment in locked.incoming_lineage_detachments
            ],
            "shared_skipped_object_keys": (
                locked.object_cleanup.shared_skipped_keys
            ),
            "shared_skipped_object_key_count": (
                locked.object_cleanup.shared_skipped_key_count
            ),
            "object_cleanup": {
                "status": cleanup_status.value,
                "deletable_keys": object_keys,
                "shared_skipped_keys": (
                    locked.object_cleanup.shared_skipped_keys
                ),
            },
        },
    )
    try:
        db.add(audit)
        db.flush()
        _retire_lot_graph_barcodes(
            db,
            user=user,
            reason=cleaned_reason,
            operation="lot_force_purge",
            lot=lot,
            pallets=pallets,
            boxes=boxes,
            files=force_purge_files,
            metadata={
                "purge_audit_id": audit.id,
                "graph_signature": locked.graph_signature,
                "force": True,
            },
        )
        deleted_counts = _apply_force_request_adjustments(
            db,
            user=user,
            audit_id=audit.id,
            plan=locked,
        )
        full_graph_counts = _delete_force_request_graph(
            db,
            request_ids=locked.fully_deleted_request_ids,
        )
        for name, count in full_graph_counts.items():
            deleted_counts[name] = deleted_counts.get(name, 0) + count
        remaining_item_references = int(
            db.scalar(
                select(func.count())
                .select_from(BoxRequestItem)
                .where(
                    or_(
                        BoxRequestItem.lot_id == lot_id,
                        (
                            BoxRequestItem.box_id.in_(all_box_ids)
                            if all_box_ids
                            else literal(False)
                        ),
                    )
                )
            )
            or 0
        )
        remaining_discrepancy_references = (
            int(
                db.scalar(
                    select(func.count())
                    .select_from(BoxRequestDiscrepancy)
                    .where(BoxRequestDiscrepancy.box_id.in_(all_box_ids))
                )
                or 0
            )
            if all_box_ids
            else 0
        )
        if remaining_item_references or remaining_discrepancy_references:
            _force_purge_conflict(
                "the locked lot graph gained an unplanned reference",
                code="graph_changed",
                preview=locked,
            )
        if pallet_ids:
            # Preserve the immutable pallet text snapshot on mixed request
            # survivors while clearing the live FK to the purged identity.
            db.execute(
                update(BoxRequestItem)
                .where(BoxRequestItem.pallet_id.in_(pallet_ids))
                .values(pallet_id=None)
                .execution_options(synchronize_session=False)
            )
        if all_box_ids:
            file_deleted_counts = _delete_file_graph_for_boxes(
                db,
                box_ids=all_box_ids,
            )
            for name, count in file_deleted_counts.items():
                deleted_counts[name] = deleted_counts.get(name, 0) + count
            deleted_counts["box_events"] = _delete_count(
                db,
                delete(BoxEvent).where(BoxEvent.box_id.in_(all_box_ids)),
            )
            deleted_counts["boxes"] = _delete_count(
                db,
                delete(Box).where(Box.id.in_(all_box_ids)),
            )
        if pallet_ids:
            deleted_counts["pallet_events"] = _delete_count(
                db,
                delete(PalletEvent).where(PalletEvent.pallet_id.in_(pallet_ids)),
            )
            deleted_counts["pallets"] = _delete_count(
                db,
                delete(Pallet).where(
                    Pallet.id.in_(pallet_ids),
                    Pallet.lot_id == lot_id,
                ),
            )
        deleted_counts["lot_events"] = _delete_count(
            db,
            delete(LotEvent).where(LotEvent.lot_id == lot_id),
        )
        deleted_counts["lots"] = _delete_count(
            db,
            delete(Lot).where(
                Lot.id == lot_id,
                Lot.version == expected_version,
            ),
        )
        if deleted_counts["lots"] != 1:
            _force_purge_conflict(
                "the locked lot changed before deletion",
                code="graph_changed",
                preview=locked,
            )
        audit.event_metadata = {
            **audit.event_metadata,
            "deleted_counts": deleted_counts,
            "impact_counts": {
                "active_boxes": locked.active_box_count,
                "archived_boxes": locked.archived_box_count,
                "active_pallets": locked.active_pallet_count,
                "archived_pallets": locked.archived_pallet_count,
                "touched_requests": locked.touched_request_count,
                "rewritten_requests": locked.request_rewrite_count,
                "deleted_requests": locked.fully_deleted_request_count,
                "lineage_detachments": (
                    locked.incoming_lineage_detachment_count
                ),
                "deletable_objects": locked.object_cleanup.deletable_key_count,
                "skipped_shared_objects": (
                    locked.object_cleanup.shared_skipped_key_count
                ),
            },
        }
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise

    return LotForcePurgeResult(
        audit_id=audit.id,
        lot_id=locked.lot_id,
        lot_name=locked.lot_name,
        lot_version=locked.lot_version,
        active_box_count=locked.active_box_count,
        archived_box_count=locked.archived_box_count,
        pallet_count=len(pallet_ids),
        touched_request_count=locked.touched_request_count,
        rewritten_request_count=locked.request_rewrite_count,
        deleted_request_count=locked.fully_deleted_request_count,
        lineage_detachment_count=locked.incoming_lineage_detachment_count,
        deletable_object_count=locked.object_cleanup.deletable_key_count,
        skipped_object_count=locked.object_cleanup.shared_skipped_key_count,
        object_cleanup_status=cleanup_status,
        object_cleanup_failures=[],
        warehouse_ids=warehouse_ids,
        file_count=len(force_purge_files),
        file_event_count=len(deleted_entity_ids["box_file_events"]),
        detached_file_snapshot_count=len(
            deleted_entity_ids["detached_request_file_snapshot_ids"]
        ),
    )


def purge_lot(
    db: Session,
    *,
    user: User,
    lot_id: int,
    confirmation_name: str,
    reason: str,
    expected_version: int,
    expected_graph_signature: str | None = None,
) -> LotPurgeResult:
    """Atomically remove one eligible lot graph and write its independent audit."""
    if user.role != UserRole.admin:
        raise LotAccessError("lot purge requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise LotRuleError("a reason is required to purge a lot")
    if len(cleaned_reason) > 2000:
        raise LotRuleError("purge reason must be at most 2000 characters")

    # This first snapshot lets execution detect a graph that changes while it is
    # waiting to acquire the deterministic Lot/Box/request/item lock set.
    prelock = analyze_lot_purge_eligibility(db, lot_id=lot_id)
    locked = analyze_lot_purge_eligibility(
        db,
        lot_id=lot_id,
        lock_for_update=True,
    )

    if locked.lot_version != expected_version:
        raise LotPurgeConflictError(
            (
                "lot version conflict: expected "
                f"{expected_version}, current {locked.lot_version}"
            ),
            code="version_conflict",
            preview=locked,
        )
    # Confirmation is intentionally strict: Unicode code points and case must
    # exactly equal the locked canonical display name. No trimming, whitespace
    # collapsing, or case folding is applied to destructive confirmation.
    if confirmation_name != locked.lot_name:
        raise LotPurgeConflictError(
            "confirmation name must exactly match the current lot name",
            code="confirmation_name_mismatch",
            preview=locked,
        )
    if (
        prelock.graph_signature != locked.graph_signature
        or (
            expected_graph_signature is not None
            and expected_graph_signature != locked.graph_signature
        )
    ):
        raise LotPurgeConflictError(
            "the lot graph changed after preview; review the latest preview",
            code="graph_changed",
            preview=locked,
        )
    if not locked.eligible:
        raise LotPurgeConflictError(
            "lot is not eligible for purge",
            code="purge_blocked",
            preview=locked,
        )

    lot = db.get(Lot, lot_id)
    assert lot is not None
    pallets = list(
        db.scalars(
            select(Pallet).where(Pallet.lot_id == lot_id).order_by(Pallet.id)
        ).all()
    )
    pallet_ids = [pallet.id for pallet in pallets]
    boxes = list(
        db.scalars(select(Box).where(Box.lot_id == lot_id).order_by(Box.id)).all()
    )
    box_ids = [box.id for box in boxes]
    request_ids = list(
        db.scalars(
            select(BoxRequestItem.request_id)
            .where(
                or_(
                    BoxRequestItem.lot_id == lot_id,
                    BoxRequestItem.box_id.in_(box_ids) if box_ids else literal(False),
                )
            )
            .distinct()
            .order_by(BoxRequestItem.request_id)
        ).all()
    )
    object_keys = _purge_object_keys(db, request_ids)
    receipt_snapshots = (
        [
            {
                "id": request.id,
                "warehouse_id": request.warehouse_id,
                "origin": _enum_text(request.origin),
                "direction": _enum_text(request.direction),
                "status": _enum_text(request.status),
                "version": request.version,
            }
            for request in db.scalars(
                select(BoxRequest)
                .where(BoxRequest.id.in_(request_ids))
                .order_by(BoxRequest.id)
            ).all()
        ]
        if request_ids
        else []
    )
    warehouse_ids = sorted(
        {
            box.current_warehouse_id for box in boxes
        }
        | {
            int(snapshot["warehouse_id"])
            for snapshot in receipt_snapshots
        }
    )
    lot_snapshot = {
        "id": lot.id,
        "barcode": lot.barcode,
        "name": lot.name,
        "normalized_name": lot.normalized_name,
        "version": lot.version,
        "created_at": lot.created_at.isoformat(),
        "updated_at": lot.updated_at.isoformat(),
        "created_by_user_id": lot.created_by_user_id,
        "updated_by_user_id": lot.updated_by_user_id,
    }
    box_snapshots = [
        {
            "id": box.id,
            "barcode": box.barcode,
            "box_number": box.box_number,
            "lot_id": box.lot_id,
            "lot_barcode": lot.barcode,
            "pallet_id": box.pallet_id,
            "pallet_barcode": (
                box.pallet.barcode if box.pallet is not None else None
            ),
            "warehouse_id": box.current_warehouse_id,
            "status": _enum_text(box.status),
            "archived_at": (
                box.archived_at.isoformat() if box.archived_at is not None else None
            ),
        }
        for box in boxes
    ]
    purge_files = list(
        db.scalars(
            select(BoxFile)
            .where(BoxFile.box_id.in_(box_ids))
            .order_by(BoxFile.id)
        ).all()
        if box_ids
        else []
    )
    purge_file_ids = [file.id for file in purge_files]
    purge_file_event_counts = dict.fromkeys(purge_file_ids, 0)
    if purge_file_ids:
        for file_id, count in db.execute(
            select(BoxFileEvent.file_id, func.count(BoxFileEvent.id))
            .where(BoxFileEvent.file_id.in_(purge_file_ids))
            .group_by(BoxFileEvent.file_id)
        ).all():
            purge_file_event_counts[int(file_id)] = int(count)
    purge_file_snapshots = [
        {
            "id": file.id,
            "lot_id": file.lot_id,
            "lot_barcode": file.lot.barcode,
            "box_id": file.box_id,
            "box_barcode": file.box.barcode,
            "pallet_id": file.box.pallet_id,
            "pallet_barcode": (
                file.box.pallet.barcode if file.box.pallet is not None else None
            ),
            "reference": file.reference,
            "normalized_reference": file.normalized_reference,
            "description": file.description,
            "barcode": file.barcode,
            "position": file.position,
            "archived_at": (
                file.archived_at.isoformat()
                if file.archived_at is not None
                else None
            ),
            "event_count": purge_file_event_counts[file.id],
        }
        for file in purge_files
    ]
    pallet_snapshots = _pallet_purge_snapshots(pallets, boxes)

    cleanup_status = (
        LotPurgeCleanupStatus.pending
        if object_keys
        else LotPurgeCleanupStatus.not_required
    )
    audit = LotPurgeEvent(
        lot_id=lot.id,
        lot_name=lot.name,
        lot_version=lot.version,
        actor_user_id=user.id,
        reason=cleaned_reason,
        receipt_ids=request_ids,
        receipt_count=len(request_ids),
        archived_box_ids=box_ids,
        archived_box_count=len(box_ids),
        pallet_ids=pallet_ids,
        pallet_count=len(pallet_ids),
        pallet_snapshots=pallet_snapshots,
        object_keys=object_keys,
        object_key_count=len(object_keys),
        object_cleanup_status=cleanup_status,
        object_cleanup_completed_at=(
            datetime.now(UTC)
            if cleanup_status == LotPurgeCleanupStatus.not_required
            else None
        ),
        event_metadata={
            "operation": "lot_purge",
            "confirmation_policy": "exact_case_sensitive_no_normalization",
            "graph_signature": locked.graph_signature,
            "warehouse_ids": warehouse_ids,
            "lot_snapshot": lot_snapshot,
            "box_snapshots": box_snapshots,
            "file_count": len(purge_files),
            "active_file_count": sum(
                file.archived_at is None for file in purge_files
            ),
            "archived_file_count": sum(
                file.archived_at is not None for file in purge_files
            ),
            "file_event_count": sum(purge_file_event_counts.values()),
            "file_snapshots": purge_file_snapshots,
            "file_cleanup_plan": {
                "detach_request_snapshot_links_with_set_null": True,
                "delete_file_events_first": True,
                "hard_delete_file_ids": purge_file_ids,
            },
            "pallet_ids": pallet_ids,
            "pallet_count": len(pallet_ids),
            "pallet_snapshots": pallet_snapshots,
            "pallet_cleanup_plan": {
                "delete_pallet_event_rows_first": True,
                "hard_delete_pallet_ids": pallet_ids,
                "scope_lot_id": lot_id,
            },
            "receipt_snapshots": receipt_snapshots,
        },
    )
    try:
        db.add(audit)
        db.flush()
        _retire_lot_graph_barcodes(
            db,
            user=user,
            reason=cleaned_reason,
            operation="lot_purge",
            lot=lot,
            pallets=pallets,
            boxes=boxes,
            files=purge_files,
            metadata={
                "purge_audit_id": audit.id,
                "graph_signature": locked.graph_signature,
                "force": False,
            },
        )
        deleted_counts = _delete_purge_graph(
            db,
            lot_id=lot_id,
            box_ids=box_ids,
            pallet_ids=pallet_ids,
            request_ids=request_ids,
            expected_version=expected_version,
            locked=locked,
        )
        audit.event_metadata = {
            **audit.event_metadata,
            "deleted_counts": deleted_counts,
        }
        db.flush()
        db.commit()
    except Exception:
        db.rollback()
        raise

    return LotPurgeResult(
        audit_id=audit.id,
        lot_id=lot_id,
        lot_name=locked.lot_name,
        lot_version=locked.lot_version,
        archived_box_count=len(box_ids),
        pallet_count=len(pallet_ids),
        receipt_count=len(request_ids),
        object_key_count=len(object_keys),
        object_cleanup_status=cleanup_status,
        object_cleanup_failures=[],
        warehouse_ids=warehouse_ids,
        file_count=len(purge_files),
        file_event_count=sum(purge_file_event_counts.values()),
        detached_file_snapshot_count=int(
            deleted_counts.get(
                "box_request_item_file_snapshot_links_detached",
                0,
            )
        ),
    )


def cleanup_lot_purge_objects(
    db: Session,
    *,
    user: User,
    audit_id: int,
) -> LotPurgeCleanupResult:
    """Idempotently retry pending object deletion without touching purge data."""
    if user.role != UserRole.admin:
        raise LotAccessError("lot purge cleanup requires admin role")
    audit = db.scalar(
        select(LotPurgeEvent)
        .where(LotPurgeEvent.id == audit_id)
        .with_for_update(of=LotPurgeEvent)
    )
    if audit is None:
        raise LotPurgeNotFoundError(f"lot purge audit {audit_id} not found")
    if audit.object_cleanup_status in (
        LotPurgeCleanupStatus.not_required,
        LotPurgeCleanupStatus.completed,
    ):
        return LotPurgeCleanupResult(
            audit_id=audit.id,
            status=audit.object_cleanup_status,
            failures=list(audit.object_cleanup_failures),
        )

    failed_keys = [
        str(failure["object_key"])
        for failure in audit.object_cleanup_failures
        if isinstance(failure, dict) and failure.get("object_key")
    ]
    target_keys = (
        failed_keys
        if failed_keys
        and audit.object_cleanup_status
        in (LotPurgeCleanupStatus.partial_failure, LotPurgeCleanupStatus.failed)
        else [str(key) for key in audit.object_keys]
    )
    audit.object_cleanup_status = LotPurgeCleanupStatus.in_progress
    db.commit()

    failures: list[dict[str, object]] = []
    for object_key in target_keys:
        if _purge_object_key_reference_count(db, object_key):
            failures.append(
                {
                    "object_key": object_key,
                    "error": "object storage deletion skipped because the key is still referenced",
                }
            )
            continue
        try:
            delete_document_strict(object_key)
        except Exception:
            failures.append(
                {
                    "object_key": object_key,
                    "error": "object storage deletion failed",
                }
            )

    audit = db.scalar(
        select(LotPurgeEvent)
        .where(LotPurgeEvent.id == audit_id)
        .with_for_update(of=LotPurgeEvent)
    )
    if audit is None:
        raise LotPurgeNotFoundError(f"lot purge audit {audit_id} not found")
    # Another idempotent worker may have completed the same keys while this
    # worker was outside the transaction performing network I/O. Completion is
    # monotonic: a slower failing worker must never overwrite confirmed success.
    if audit.object_cleanup_status in (
        LotPurgeCleanupStatus.not_required,
        LotPurgeCleanupStatus.completed,
    ):
        return LotPurgeCleanupResult(
            audit_id=audit.id,
            status=audit.object_cleanup_status,
            failures=list(audit.object_cleanup_failures),
        )
    audit.object_cleanup_failures = failures
    if not failures:
        audit.object_cleanup_status = LotPurgeCleanupStatus.completed
        audit.object_cleanup_completed_at = datetime.now(UTC)
    elif len(failures) >= audit.object_key_count:
        audit.object_cleanup_status = LotPurgeCleanupStatus.failed
        audit.object_cleanup_completed_at = None
    else:
        audit.object_cleanup_status = LotPurgeCleanupStatus.partial_failure
        audit.object_cleanup_completed_at = None
    audit.event_metadata = {
        **audit.event_metadata,
        "object_cleanup_attempted_at": datetime.now(UTC).isoformat(),
        "object_cleanup_attempted_key_count": len(target_keys),
        "object_cleanup_failure_count": len(failures),
    }
    db.commit()
    return LotPurgeCleanupResult(
        audit_id=audit.id,
        status=audit.object_cleanup_status,
        failures=list(audit.object_cleanup_failures),
    )


_OVERLAP_LIST_LIMIT = 100


def _collision_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _box_signature_value(box: Box) -> dict[str, object]:
    return {
        "id": box.id,
        "lot_id": box.lot_id,
        "pallet_id": box.pallet_id,
        "box_number": box.box_number,
        "warehouse_id": box.current_warehouse_id,
        "status": box.status.value,
        "archived_at": _collision_timestamp(box.archived_at),
    }


def _pallet_signature_value(pallet: Pallet) -> dict[str, object]:
    return {
        "id": pallet.id,
        "lot_id": pallet.lot_id,
        "pallet_number": pallet.pallet_number,
        "normalized_pallet_number": pallet.normalized_pallet_number,
        "version": pallet.version,
        "is_active": pallet.is_active,
        "archived_at": _collision_timestamp(pallet.archived_at),
        "absorbed_into_pallet_id": pallet.absorbed_into_pallet_id,
        "absorbed_at": _collision_timestamp(pallet.absorbed_at),
    }


def _box_event_signature_value(event: BoxEvent) -> dict[str, object]:
    """Return durable, non-sensitive BoxEvent facts used by merge confirmation."""
    return {
        "id": event.id,
        "box_id": event.box_id,
        "warehouse_id": event.warehouse_id,
        "event_type": event.event_type.value,
        "from_status": event.from_status.value if event.from_status else None,
        "to_status": event.to_status.value if event.to_status else None,
        "from_warehouse_id": event.from_warehouse_id,
        "to_warehouse_id": event.to_warehouse_id,
        "occurred_at": _collision_timestamp(event.occurred_at),
        "user_id": event.user_id,
    }


def _box_link_counts(
    db: Session,
    box_ids: list[int],
    model: type[BoxRequestItem] | type[BoxRequestDiscrepancy],
) -> dict[int, int]:
    if not box_ids:
        return {}
    return {
        int(box_id): int(count)
        for box_id, count in db.execute(
            select(model.box_id, func.count(model.id))
            .where(model.box_id.in_(box_ids))
            .group_by(model.box_id)
            .order_by(model.box_id)
        ).all()
        if box_id is not None
    }


def _merge_box_lock_statement(lot_ids: list[int]):
    """Lock both lots' complete inventory with an explicit PostgreSQL target."""
    return (
        select(Box)
        .where(Box.lot_id.in_(sorted(set(lot_ids))))
        .order_by(Box.id)
        .with_for_update(of=Box)
        .execution_options(populate_existing=True)
    )


def _merge_pallet_lock_statement(lot_ids: list[int]):
    """Lock both lots' complete pallet topology before physical boxes."""
    return (
        select(Pallet)
        .where(Pallet.lot_id.in_(sorted(set(lot_ids))))
        .order_by(Pallet.id)
        .with_for_update(of=Pallet)
        .execution_options(populate_existing=True)
    )


def _merge_pallet_event_lock_statement(pallet_ids: list[int]):
    return (
        select(PalletEvent)
        .where(PalletEvent.pallet_id.in_(sorted(set(pallet_ids))))
        .order_by(PalletEvent.id)
        .with_for_update(of=PalletEvent)
        .execution_options(populate_existing=True)
    )


def _merge_request_item_lock_statement(
    source_lot_id: int,
    removed_box_ids: list[int],
    source_pallet_ids: list[int] | None = None,
):
    affected = BoxRequestItem.lot_id == source_lot_id
    if removed_box_ids:
        affected = or_(affected, BoxRequestItem.box_id.in_(removed_box_ids))
    if source_pallet_ids:
        affected = or_(
            affected,
            BoxRequestItem.pallet_id.in_(source_pallet_ids),
        )
    return (
        select(BoxRequestItem)
        .where(affected)
        .order_by(BoxRequestItem.id)
        .with_for_update(of=BoxRequestItem)
        .execution_options(populate_existing=True)
    )


def _merge_discrepancy_lock_statement(removed_box_ids: list[int]):
    return (
        select(BoxRequestDiscrepancy)
        .where(BoxRequestDiscrepancy.box_id.in_(sorted(set(removed_box_ids))))
        .order_by(BoxRequestDiscrepancy.id)
        .with_for_update(of=BoxRequestDiscrepancy)
        .execution_options(populate_existing=True)
    )


def _merge_request_lock_statement(request_ids: list[int]):
    return (
        select(BoxRequest.id)
        .where(BoxRequest.id.in_(sorted(set(request_ids))))
        .order_by(BoxRequest.id)
        .with_for_update(of=BoxRequest)
    )


def _merge_box_event_lock_statement(box_ids: list[int]):
    return (
        select(BoxEvent)
        .where(BoxEvent.box_id.in_(sorted(set(box_ids))))
        .order_by(BoxEvent.id)
        .with_for_update(of=BoxEvent)
        .execution_options(populate_existing=True)
    )


def _merge_signature_links(
    db: Session,
    *,
    lot_ids: tuple[int, int],
    box_ids: list[int],
) -> tuple[list[dict[str, int | None]], list[dict[str, int | None]]]:
    item_filter = BoxRequestItem.lot_id.in_(lot_ids)
    if box_ids:
        item_filter = or_(item_filter, BoxRequestItem.box_id.in_(box_ids))
    item_links = [
        {
            "id": int(item_id),
            "request_id": int(request_id),
            "box_id": int(box_id) if box_id is not None else None,
            "lot_id": int(lot_id) if lot_id is not None else None,
            "pallet_id": int(pallet_id) if pallet_id is not None else None,
        }
        for item_id, request_id, box_id, lot_id, pallet_id in db.execute(
            select(
                BoxRequestItem.id,
                BoxRequestItem.request_id,
                BoxRequestItem.box_id,
                BoxRequestItem.lot_id,
                BoxRequestItem.pallet_id,
            )
            .where(item_filter)
            .order_by(BoxRequestItem.id)
        ).all()
    ]
    discrepancy_links = (
        [
            {
                "id": int(discrepancy_id),
                "request_id": int(request_id),
                "request_item_id": (
                    int(request_item_id) if request_item_id is not None else None
                ),
                "box_id": int(box_id) if box_id is not None else None,
            }
            for discrepancy_id, request_id, request_item_id, box_id in db.execute(
                select(
                    BoxRequestDiscrepancy.id,
                    BoxRequestDiscrepancy.request_id,
                    BoxRequestDiscrepancy.request_item_id,
                    BoxRequestDiscrepancy.box_id,
                )
                .where(BoxRequestDiscrepancy.box_id.in_(box_ids))
                .order_by(BoxRequestDiscrepancy.id)
            ).all()
        ]
        if box_ids
        else []
    )
    return item_links, discrepancy_links


def _merge_candidate(
    db: Session,
    source: Lot,
    target: Lot,
) -> LotMergeCandidate:
    """Build a deterministic collision plan across active and archived boxes."""
    boxes = list(
        db.scalars(
            select(Box)
            .where(Box.lot_id.in_((source.id, target.id)))
            .order_by(Box.box_number, Box.lot_id, Box.id)
        ).all()
    )
    files = list(
        db.scalars(
            select(BoxFile)
            .where(BoxFile.lot_id.in_((source.id, target.id)))
            .order_by(BoxFile.normalized_reference, BoxFile.lot_id, BoxFile.id)
        ).all()
    )
    pallets = list(
        db.scalars(
            select(Pallet)
            .where(Pallet.lot_id.in_((source.id, target.id)))
            .order_by(
                Pallet.normalized_pallet_number,
                Pallet.lot_id,
                Pallet.id,
            )
        ).all()
    )
    target_pallets = {
        pallet.normalized_pallet_number: pallet
        for pallet in pallets
        if pallet.lot_id == target.id
    }
    pallet_box_counts: dict[int, int] = {}
    for box in boxes:
        if box.pallet_id is not None:
            pallet_box_counts[box.pallet_id] = pallet_box_counts.get(box.pallet_id, 0) + 1
    pallet_collisions: list[LotPalletCollision] = []
    pallet_actions: list[LotPalletMergeAction] = []
    for source_pallet in (pallet for pallet in pallets if pallet.lot_id == source.id):
        target_pallet = target_pallets.get(source_pallet.normalized_pallet_number)
        if target_pallet is None:
            pallet_actions.append(
                LotPalletMergeAction(
                    source_pallet_id=source_pallet.id,
                    source_pallet_number=source_pallet.pallet_number,
                    source_is_active=source_pallet.is_active,
                    action="transfer",
                    box_count=pallet_box_counts.get(source_pallet.id, 0),
                )
            )
            continue
        collision_reason: Literal["inactive_target"] | None = None
        if not target_pallet.is_active:
            collision_reason = "inactive_target"
        if collision_reason is not None:
            pallet_collisions.append(
                LotPalletCollision(
                    normalized_pallet_number=source_pallet.normalized_pallet_number,
                    source_pallet_id=source_pallet.id,
                    source_pallet_number=source_pallet.pallet_number,
                    source_is_active=source_pallet.is_active,
                    target_pallet_id=target_pallet.id,
                    target_pallet_number=target_pallet.pallet_number,
                    target_is_active=target_pallet.is_active,
                    reason=collision_reason,
                )
            )
            continue
        pallet_actions.append(
            LotPalletMergeAction(
                source_pallet_id=source_pallet.id,
                source_pallet_number=source_pallet.pallet_number,
                source_is_active=source_pallet.is_active,
                action="combine",
                target_pallet_id=target_pallet.id,
                box_count=pallet_box_counts.get(source_pallet.id, 0),
            )
        )
    boxes_by_number: dict[str, dict[LotMergeSide, list[Box]]] = {}
    for box in boxes:
        side: LotMergeSide = "source" if box.lot_id == source.id else "target"
        boxes_by_number.setdefault(
            box.box_number, {"source": [], "target": []}
        )[side].append(box)

    shared = [
        (number, grouped["source"], grouped["target"])
        for number, grouped in sorted(boxes_by_number.items())
        if grouped["source"] and grouped["target"]
    ]

    decisions: list[
        tuple[str, list[Box], list[Box], Box | None, Box | None]
    ] = []
    removed_box_ids: list[int] = []
    for number, source_boxes, target_boxes in shared:
        survivor: Box | None = None
        removed: Box | None = None
        if len(source_boxes) == 1 and len(target_boxes) == 1:
            source_box = source_boxes[0]
            target_box = target_boxes[0]
            source_active = source_box.archived_at is None
            target_active = target_box.archived_at is None
            if not (source_active and target_active):
                if source_active:
                    survivor, removed = source_box, target_box
                elif target_active:
                    survivor, removed = target_box, source_box
                else:
                    survivor, removed = target_box, source_box
                removed_box_ids.append(removed.id)
        decisions.append((number, source_boxes, target_boxes, survivor, removed))

    removed_box_id_set = set(removed_box_ids)
    files_by_reference: dict[str, dict[LotMergeSide, list[BoxFile]]] = {}
    for file in files:
        side: LotMergeSide = "source" if file.lot_id == source.id else "target"
        files_by_reference.setdefault(
            file.normalized_reference,
            {"source": [], "target": []},
        )[side].append(file)
    file_collisions: list[LotFileReferenceCollision] = []
    archived_file_collisions: list[LotFileReferenceCollision] = []
    for normalized_reference, grouped in sorted(files_by_reference.items()):
        source_files = grouped["source"]
        target_files = grouped["target"]
        if not source_files or not target_files:
            continue
        all_files = source_files + target_files
        active_ids = sorted(
            file.id for file in all_files if file.archived_at is None
        )
        archived_only = not active_ids
        survivor: BoxFile | None = None
        removed_files: list[BoxFile] = []
        if archived_only:
            preservable_source = [
                file for file in source_files if file.box_id not in removed_box_id_set
            ]
            preservable_target = [
                file for file in target_files if file.box_id not in removed_box_id_set
            ]
            if preservable_target:
                survivor = preservable_target[0]
            elif preservable_source:
                survivor = preservable_source[0]
            else:
                survivor = target_files[0]
            removed_files = [file for file in all_files if file.id != survivor.id]
        collision = LotFileReferenceCollision(
            normalized_reference=normalized_reference,
            source_file_ids=[file.id for file in source_files],
            target_file_ids=[file.id for file in target_files],
            active_file_ids=active_ids,
            archived_only=archived_only,
            survivor_file_id=survivor.id if survivor is not None else None,
            removed_file_ids=sorted(file.id for file in removed_files),
        )
        file_collisions.append(collision)
        if archived_only:
            archived_file_collisions.append(collision)

    item_counts = _box_link_counts(db, removed_box_ids, BoxRequestItem)
    discrepancy_counts = _box_link_counts(
        db, removed_box_ids, BoxRequestDiscrepancy
    )
    removed_box_events = (
        list(
            db.scalars(
                select(BoxEvent)
                .where(BoxEvent.box_id.in_(removed_box_ids))
                .order_by(BoxEvent.id)
            ).all()
        )
        if removed_box_ids
        else []
    )
    event_counts: dict[int, int] = {box_id: 0 for box_id in removed_box_ids}
    for event in removed_box_events:
        event_counts[event.box_id] += 1
    file_counts = {
        box_id: sum(file.box_id == box_id for file in files)
        for box_id in removed_box_ids
    }
    file_event_counts = dict.fromkeys(removed_box_ids, 0)
    if removed_box_ids:
        for box_id, count in db.execute(
            select(BoxFile.box_id, func.count(BoxFileEvent.id))
            .join(BoxFileEvent, BoxFileEvent.file_id == BoxFile.id)
            .where(BoxFile.box_id.in_(removed_box_ids))
            .group_by(BoxFile.box_id)
        ).all():
            file_event_counts[int(box_id)] = int(count)


    resolvable: list[LotArchivedBoxCollision] = []
    hard: list[LotHardBoxOverlap] = []
    signature_collisions: list[dict[str, object]] = []
    for number, source_boxes, target_boxes, survivor, removed in decisions:
        if survivor is None or removed is None:
            hard_entry = LotHardBoxOverlap(
                box_number=number,
                source_box_ids=[box.id for box in source_boxes],
                target_box_ids=[box.id for box in target_boxes],
                source_active_box_ids=[
                    box.id for box in source_boxes if box.archived_at is None
                ],
                target_active_box_ids=[
                    box.id for box in target_boxes if box.archived_at is None
                ],
            )
            hard.append(hard_entry)
            signature_collisions.append(
                {
                    "box_number": number,
                    "source_boxes": [
                        _box_signature_value(box) for box in source_boxes
                    ],
                    "target_boxes": [
                        _box_signature_value(box) for box in target_boxes
                    ],
                    "decision": "hard_overlap",
                }
            )
            continue

        survivor_side: LotMergeSide = (
            "source" if survivor.lot_id == source.id else "target"
        )
        removed_side: LotMergeSide = (
            "source" if removed.lot_id == source.id else "target"
        )
        entry = LotArchivedBoxCollision(
            box_number=number,
            source_box_id=source_boxes[0].id,
            source_box_archived=source_boxes[0].archived_at is not None,
            target_box_id=target_boxes[0].id,
            target_box_archived=target_boxes[0].archived_at is not None,
            survivor_box_id=survivor.id,
            survivor_lot_side=survivor_side,
            removed_box_id=removed.id,
            removed_lot_side=removed_side,
            request_item_relink_count=item_counts.get(removed.id, 0),
            discrepancy_relink_count=discrepancy_counts.get(removed.id, 0),
            box_event_delete_count=event_counts.get(removed.id, 0),
            file_delete_count=file_counts.get(removed.id, 0),
            file_event_delete_count=file_event_counts.get(removed.id, 0),
        )
        resolvable.append(entry)
        signature_collisions.append(
            {
                "box_number": number,
                "source_boxes": [
                    _box_signature_value(box) for box in source_boxes
                ],
                "target_boxes": [
                    _box_signature_value(box) for box in target_boxes
                ],
                "decision": {
                    "survivor_box_id": entry.survivor_box_id,
                    "survivor_lot_side": entry.survivor_lot_side,
                    "removed_box_id": entry.removed_box_id,
                    "removed_lot_side": entry.removed_lot_side,
                    "request_item_relink_count": entry.request_item_relink_count,
                    "discrepancy_relink_count": entry.discrepancy_relink_count,
                    "box_event_delete_count": entry.box_event_delete_count,
                    "file_delete_count": entry.file_delete_count,
                    "file_event_delete_count": entry.file_event_delete_count,
                },
            }
        )

    item_links, discrepancy_links = _merge_signature_links(
        db,
        lot_ids=(source.id, target.id),
        box_ids=[box.id for box in boxes],
    )
    removed_box_id_set = set(removed_box_ids)
    affected_request_ids = sorted(
        {
            int(link["request_id"])
            for link in item_links
            if link["lot_id"] == source.id
            or link["box_id"] in removed_box_id_set
        }
        | {
            int(link["request_id"])
            for link in discrepancy_links
            if link["box_id"] in removed_box_id_set
        }
    )
    request_context = (
        [
            {
                "id": int(request_id),
                "version": int(version),
                "status": status.value,
                "direction": direction.value,
                "origin": origin.value,
                "source_inbound_request_id": (
                    int(source_inbound_request_id)
                    if source_inbound_request_id is not None
                    else None
                ),
                "parent_request_id": (
                    int(parent_request_id) if parent_request_id is not None else None
                ),
                "root_request_id": (
                    int(root_request_id) if root_request_id is not None else None
                ),
            }
            for (
                request_id,
                version,
                status,
                direction,
                origin,
                source_inbound_request_id,
                parent_request_id,
                root_request_id,
            ) in db.execute(
                select(
                    BoxRequest.id,
                    BoxRequest.version,
                    BoxRequest.status,
                    BoxRequest.direction,
                    BoxRequest.origin,
                    BoxRequest.source_inbound_request_id,
                    BoxRequest.parent_request_id,
                    BoxRequest.root_request_id,
                )
                .where(BoxRequest.id.in_(affected_request_ids))
                .order_by(BoxRequest.id)
            ).all()
        ]
        if affected_request_ids
        else []
    )
    pallet_event_context = (
        [
            {
                "id": int(event_id),
                "pallet_id": int(pallet_id),
                "event_type": event_type.value,
                "old_pallet_number": old_number,
                "new_pallet_number": new_number,
                "from_warehouse_id": from_warehouse_id,
                "to_warehouse_id": to_warehouse_id,
                "occurred_at": _collision_timestamp(occurred_at),
                "metadata": event_metadata,
            }
            for (
                event_id,
                pallet_id,
                event_type,
                old_number,
                new_number,
                from_warehouse_id,
                to_warehouse_id,
                occurred_at,
                event_metadata,
            ) in db.execute(
                select(
                    PalletEvent.id,
                    PalletEvent.pallet_id,
                    PalletEvent.event_type,
                    PalletEvent.old_pallet_number,
                    PalletEvent.new_pallet_number,
                    PalletEvent.from_warehouse_id,
                    PalletEvent.to_warehouse_id,
                    PalletEvent.occurred_at,
                    PalletEvent.event_metadata,
                )
                .where(PalletEvent.pallet_id.in_([pallet.id for pallet in pallets]))
                .order_by(PalletEvent.id)
            ).all()
        ]
        if pallets
        else []
    )
    signature_payload = {
        "source": {"id": source.id, "version": source.version},
        "target": {"id": target.id, "version": target.version},
        "pallets": [_pallet_signature_value(pallet) for pallet in pallets],
        "pallet_actions": [
            {
                "source_pallet_id": action.source_pallet_id,
                "action": action.action,
                "target_pallet_id": action.target_pallet_id,
                "box_count": action.box_count,
            }
            for action in pallet_actions
        ],
        "pallet_collisions": [
            {
                "source_pallet_id": collision.source_pallet_id,
                "target_pallet_id": collision.target_pallet_id,
                "reason": collision.reason,
            }
            for collision in pallet_collisions
        ],
        "pallet_events": pallet_event_context,
        "inventory": [_box_signature_value(box) for box in boxes],
        "files": [
            [
                file.id,
                file.lot_id,
                file.box_id,
                file.normalized_reference,
                file.position,
                _collision_timestamp(file.archived_at),
                file.version,
            ]
            for file in files
        ],
        "file_collisions": [
            {
                "normalized_reference": collision.normalized_reference,
                "source_file_ids": collision.source_file_ids,
                "target_file_ids": collision.target_file_ids,
                "active_file_ids": collision.active_file_ids,
                "survivor_file_id": collision.survivor_file_id,
                "removed_file_ids": collision.removed_file_ids,
            }
            for collision in file_collisions
        ],
        "request_item_links": item_links,
        "discrepancy_links": discrepancy_links,
        "request_context": request_context,
        "removed_box_events": [
            _box_event_signature_value(event) for event in removed_box_events
        ],
        "collisions": signature_collisions,
    }
    signature_json = json.dumps(
        signature_payload, sort_keys=True, separators=(",", ":")
    )
    collision_signature = hashlib.sha256(signature_json.encode()).hexdigest()
    overlap_numbers = [number for number, *_rest in shared]
    overlap_count = len(overlap_numbers)
    resolvable_count = len(resolvable)
    hard_count = len(hard)
    pallet_collision_count = len(pallet_collisions)
    pallet_action_count = len(pallet_actions)
    active_file_collision_count = sum(
        not collision.archived_only for collision in file_collisions
    )
    archived_file_collision_count = len(archived_file_collisions)
    return LotMergeCandidate(
        source_id=source.id,
        source_barcode=source.barcode,
        source_name=source.name,
        source_version=source.version,
        target_id=target.id,
        target_barcode=target.barcode,
        target_name=target.name,
        target_version=target.version,
        merge_allowed=(
            overlap_count == 0
            and pallet_collision_count == 0
            and not file_collisions
        ),
        overlapping_box_numbers=overlap_numbers[:_OVERLAP_LIST_LIMIT],
        overlapping_box_count=overlap_count,
        overlap_list_truncated=overlap_count > _OVERLAP_LIST_LIMIT,
        resolvable_archived_collisions=resolvable[:_OVERLAP_LIST_LIMIT],
        resolvable_archived_collision_count=resolvable_count,
        resolvable_archived_collisions_truncated=(
            resolvable_count > _OVERLAP_LIST_LIMIT
        ),
        hard_overlaps=hard[:_OVERLAP_LIST_LIMIT],
        hard_overlap_count=hard_count,
        hard_overlaps_truncated=hard_count > _OVERLAP_LIST_LIMIT,
        merge_allowed_with_archived_overwrite=(
            resolvable_count > 0
            or archived_file_collision_count > 0
        )
        and hard_count == 0
        and pallet_collision_count == 0
        and active_file_collision_count == 0,
        requires_explicit_overwrite=(
            resolvable_count > 0 or archived_file_collision_count > 0
        ),
        collision_signature=collision_signature,
        pallet_collisions=pallet_collisions[:_OVERLAP_LIST_LIMIT],
        pallet_collision_count=pallet_collision_count,
        pallet_collisions_truncated=pallet_collision_count > _OVERLAP_LIST_LIMIT,
        pallet_actions=pallet_actions[:_OVERLAP_LIST_LIMIT],
        pallet_action_count=pallet_action_count,
        pallet_actions_truncated=pallet_action_count > _OVERLAP_LIST_LIMIT,
        file_reference_collisions=file_collisions[:_OVERLAP_LIST_LIMIT],
        file_reference_collision_count=len(file_collisions),
        file_reference_collisions_truncated=(
            len(file_collisions) > _OVERLAP_LIST_LIMIT
        ),
        active_file_reference_collision_count=active_file_collision_count,
        archived_file_reference_collision_count=archived_file_collision_count,
        all_resolvable_archived_collisions=resolvable,
        all_archived_file_collisions=archived_file_collisions,
    )


def rename_lot(
    db: Session,
    *,
    user: User,
    lot_id: int,
    new_name: str,
    reason: str,
    expected_version: int,
    commit: bool = True,
) -> Lot:
    """Globally rename one lot without rewriting historical snapshots."""
    if user.role != UserRole.admin:
        raise LotAccessError("lot rename requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise LotRuleError("a reason is required to rename a lot")
    cleaned_name = validate_lot_name(new_name)
    normalized = normalize_lot_name(cleaned_name)
    lot = db.scalar(select(Lot).where(Lot.id == lot_id).with_for_update())
    if lot is None:
        raise LotNotFoundError(f"lot {lot_id} not found")
    if lot.merged_into_lot_id is not None:
        raise LotConflictError(f"lot {lot_id} has already been merged")
    if lot.version != expected_version:
        raise LotVersionConflictError(
            f"lot version conflict: expected {expected_version}, current {lot.version}"
        )
    collision = db.scalar(
        select(Lot).where(
            Lot.normalized_name == normalized,
            Lot.id != lot.id,
            Lot.merged_into_lot_id.is_(None),
        )
    )
    if collision is not None:
        raise LotNameCollisionError(
            f"lot {collision.name!r} already exists",
            _merge_candidate(db, lot, collision),
        )
    old_name = lot.name
    if old_name == cleaned_name:
        return lot
    try:
        with db.begin_nested():
            lot.name = cleaned_name
            lot.updated_by_user_id = user.id
            db.add(
                LotEvent(
                    lot_id=lot.id,
                    event_type=LotEventType.renamed,
                    old_name=old_name,
                    new_name=cleaned_name,
                    actor_user_id=user.id,
                    reason=cleaned_reason,
                    event_metadata={
                        "operation": "global_rename",
                        "expected_version": expected_version,
                    },
                )
            )
            db.flush()
    except IntegrityError as exc:
        collision = db.scalar(
            select(Lot).where(
                Lot.normalized_name == normalized,
                Lot.id != lot.id,
                Lot.merged_into_lot_id.is_(None),
            )
        )
        if collision is not None:
            raise LotNameCollisionError(
                f"lot {collision.name!r} already exists",
                _merge_candidate(db, lot, collision),
            ) from exc
        raise LotConflictError(
            f"lot {cleaned_name!r} changed concurrently; retry the operation"
        ) from exc
    if commit:
        db.commit()
        db.refresh(lot)
    return lot


def _bounded_merge_ids(ids: list[int]) -> dict[str, object]:
    ordered = sorted(set(ids))
    return {
        "ids": ordered[:_OVERLAP_LIST_LIMIT],
        "count": len(ordered),
        "truncated": len(ordered) > _OVERLAP_LIST_LIMIT,
    }


def merge_lots(
    db: Session,
    *,
    user: User,
    source_lot_id: int,
    target_lot_id: int,
    reason: str,
    expected_source_version: int,
    expected_target_version: int,
    overwrite_archived_collisions: bool = False,
    expected_collision_signature: str | None = None,
    commit: bool = True,
) -> LotMergeResult:
    """Merge source into target after locking and revalidating the full graph."""
    if user.role != UserRole.admin:
        raise LotAccessError("lot merge requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise LotRuleError("a reason is required to merge lots")
    if not overwrite_archived_collisions and expected_collision_signature is not None:
        raise LotRuleError(
            "expected_collision_signature is only allowed when "
            "overwrite_archived_collisions is true"
        )
    if source_lot_id == target_lot_id:
        raise LotMergeConflictError(
            "source and target lots must differ",
            code="invalid_merge",
        )

    ordered_ids = sorted((source_lot_id, target_lot_id))
    locked_lots = lock_lots_exclusively(db, ordered_ids)
    source = locked_lots.get(source_lot_id)
    target = locked_lots.get(target_lot_id)
    if source is None or target is None:
        raise LotMergeConflictError(
            "source or target lot does not exist",
            code="invalid_merge",
            source=source,
            target=target,
        )
    if source.merged_into_lot_id is not None or target.merged_into_lot_id is not None:
        raise LotMergeConflictError(
            "source and target must both be active, unmerged lots",
            code="already_merged",
            source=source,
            target=target,
        )

    pallets = list(db.scalars(_merge_pallet_lock_statement(ordered_ids)).all())
    if pallets:
        list(
            db.scalars(
                _merge_pallet_event_lock_statement(
                    [pallet.id for pallet in pallets]
                )
            ).all()
        )
    boxes = list(db.scalars(_merge_box_lock_statement(ordered_ids)).all())
    files = list(
        db.scalars(
            select(BoxFile)
            .where(BoxFile.lot_id.in_(ordered_ids))
            .order_by(BoxFile.id)
            .with_for_update(of=BoxFile)
            .execution_options(populate_existing=True)
        ).all()
    )
    file_ids = [file.id for file in files]
    file_events = (
        list(
            db.scalars(
                select(BoxFileEvent)
                .where(BoxFileEvent.file_id.in_(file_ids))
                .order_by(BoxFileEvent.id)
                .with_for_update(of=BoxFileEvent)
            ).all()
        )
        if file_ids
        else []
    )
    file_snapshots = (
        list(
            db.scalars(
                select(BoxRequestItemFileSnapshot)
                .where(BoxRequestItemFileSnapshot.file_id.in_(file_ids))
                .order_by(BoxRequestItemFileSnapshot.id)
                .with_for_update(of=BoxRequestItemFileSnapshot)
            ).all()
        )
        if file_ids
        else []
    )
    preliminary_candidate = _merge_candidate(db, source, target)
    removed_box_ids = sorted(
        collision.removed_box_id
        for collision in preliminary_candidate.all_resolvable_archived_collisions
    )
    request_items = list(
        db.scalars(
            _merge_request_item_lock_statement(
                source.id,
                removed_box_ids,
                [
                    pallet.id
                    for pallet in pallets
                    if pallet.lot_id == source.id
                ],
            )
        ).all()
    )
    discrepancies = (
        list(
            db.scalars(
                _merge_discrepancy_lock_statement(removed_box_ids)
            ).all()
        )
        if removed_box_ids
        else []
    )
    affected_request_ids = sorted(
        {
            item.request_id for item in request_items
        }
        | {discrepancy.request_id for discrepancy in discrepancies}
    )
    if affected_request_ids:
        list(db.scalars(_merge_request_lock_statement(affected_request_ids)).all())
    removed_box_events = (
        list(db.scalars(_merge_box_event_lock_statement(removed_box_ids)).all())
        if removed_box_ids
        else []
    )
    box_event_ids = [event.id for event in removed_box_events]

    # This is the authoritative candidate: it is rebuilt only after every row
    # that can affect the merge decision or be mutated by it is locked.
    candidate = _merge_candidate(db, source, target)
    if source.version != expected_source_version:
        raise LotMergeConflictError(
            (
                "source lot version conflict: expected "
                f"{expected_source_version}, current {source.version}"
            ),
            code="source_version_conflict",
            source=source,
            target=target,
            candidate=candidate,
        )
    if target.version != expected_target_version:
        raise LotMergeConflictError(
            (
                "target lot version conflict: expected "
                f"{expected_target_version}, current {target.version}"
            ),
            code="target_version_conflict",
            source=source,
            target=target,
            candidate=candidate,
        )
    if (
        overwrite_archived_collisions
        and expected_collision_signature != candidate.collision_signature
    ):
        raise LotMergeConflictError(
            "collision graph changed or was not acknowledged; preview the merge again",
            code="overlap_signature_mismatch",
            source=source,
            target=target,
            candidate=candidate,
        )
    if candidate.pallet_collisions:
        raise LotMergeConflictError(
            "lots have conflicting pallet identities",
            code="pallet_collision",
            source=source,
            target=target,
            candidate=candidate,
        )
    if candidate.active_file_reference_collision_count:
        raise LotMergeConflictError(
            "lots have active File reference collisions",
            code="file_reference_collision",
            source=source,
            target=target,
            candidate=candidate,
        )
    if not candidate.merge_allowed and (
        not overwrite_archived_collisions
        or not candidate.merge_allowed_with_archived_overwrite
    ):
        raise LotMergeConflictError(
            "lots have overlapping physical box numbers",
            code="box_number_overlap",
            source=source,
            target=target,
            candidate=candidate,
        )

    boxes_by_id = {box.id: box for box in boxes}
    execution_collisions = candidate.all_resolvable_archived_collisions
    current_removed_box_ids = sorted(
        collision.removed_box_id for collision in execution_collisions
    )
    if current_removed_box_ids != removed_box_ids:
        raise LotMergeConflictError(
            "collision graph changed while rows were being locked",
            code="overlap_signature_mismatch",
            source=source,
            target=target,
            candidate=candidate,
        )
    file_delete_ids = sorted(
        {
            file.id for file in files if file.box_id in set(removed_box_ids)
        }
        | {
            file_id
            for collision in candidate.all_archived_file_collisions
            for file_id in collision.removed_file_ids
        }
    )
    file_delete_id_set = set(file_delete_ids)
    deleted_file_event_ids = sorted(
        event.id for event in file_events if event.file_id in file_delete_id_set
    )
    detached_file_snapshot_ids = sorted(
        snapshot.id
        for snapshot in file_snapshots
        if snapshot.file_id in file_delete_id_set
    )
    moved_files = [
        file
        for file in files
        if file.lot_id == source.id and file.id not in file_delete_id_set
    ]
    file_delete_snapshots = [
        {
            "id": file.id,
            "lot_id": file.lot_id,
            "lot_barcode": file.lot.barcode,
            "box_id": file.box_id,
            "box_barcode": file.box.barcode,
            "reference": file.reference,
            "normalized_reference": file.normalized_reference,
            "description": file.description,
            "barcode": file.barcode,
            "position": file.position,
            "archived_at": _collision_timestamp(file.archived_at),
            "event_ids": sorted(
                event.id for event in file_events if event.file_id == file.id
            ),
            "detached_request_snapshot_ids": sorted(
                snapshot.id
                for snapshot in file_snapshots
                if snapshot.file_id == file.id
            ),
        }
        for file in files
        if file.id in file_delete_id_set
    ]
    for collision in execution_collisions:
        removed = boxes_by_id.get(collision.removed_box_id)
        survivor = boxes_by_id.get(collision.survivor_box_id)
        if removed is None or survivor is None or removed.archived_at is None:
            raise LotMergeConflictError(
                "an overwrite survivor is no longer safe",
                code="box_number_overlap",
                source=source,
                target=target,
                candidate=candidate,
            )

    pallets_by_id = {pallet.id: pallet for pallet in pallets}
    target_pallets_by_number = {
        pallet.normalized_pallet_number: pallet
        for pallet in pallets
        if pallet.lot_id == target.id
    }
    combined_pallet_targets: dict[int, Pallet] = {}
    transferred_pallets: list[Pallet] = []
    for pallet in pallets:
        if pallet.lot_id != source.id:
            continue
        matching_target = target_pallets_by_number.get(
            pallet.normalized_pallet_number
        )
        if matching_target is None:
            transferred_pallets.append(pallet)
        else:
            combined_pallet_targets[pallet.id] = matching_target

    removed_to_survivor = {
        collision.removed_box_id: collision.survivor_box_id
        for collision in execution_collisions
    }
    event_ids_by_box: dict[int, list[int]] = {
        removed_id: [] for removed_id in removed_box_ids
    }
    box_events_by_box: dict[int, list[BoxEvent]] = {
        removed_id: [] for removed_id in removed_box_ids
    }
    for event in removed_box_events:
        event_ids_by_box[event.box_id].append(event.id)
        box_events_by_box[event.box_id].append(event)
    items_by_removed_box: dict[int, list[BoxRequestItem]] = {
        box_id: [] for box_id in removed_box_ids
    }
    for item in request_items:
        if item.box_id in items_by_removed_box:
            items_by_removed_box[item.box_id].append(item)
    discrepancies_by_removed_box: dict[int, list[BoxRequestDiscrepancy]] = {
        box_id: [] for box_id in removed_box_ids
    }
    for discrepancy in discrepancies:
        if discrepancy.box_id in discrepancies_by_removed_box:
            discrepancies_by_removed_box[discrepancy.box_id].append(discrepancy)

    collision_snapshots: list[dict[str, object]] = []
    for collision in execution_collisions:
        survivor = boxes_by_id[collision.survivor_box_id]
        removed = boxes_by_id[collision.removed_box_id]
        collision_item_ids = [
            item.id for item in items_by_removed_box[removed.id]
        ]
        collision_discrepancy_ids = [
            discrepancy.id
            for discrepancy in discrepancies_by_removed_box[removed.id]
        ]
        collision_event_ids = event_ids_by_box[removed.id]
        collision_snapshots.append(
            {
                "box_number": collision.box_number,
                "survivor": {
                    "box_id": survivor.id,
                    "box_barcode": survivor.barcode,
                    "box_number": survivor.box_number,
                    "lot_side": collision.survivor_lot_side,
                    "status": survivor.status.value,
                    "archived_at": _collision_timestamp(survivor.archived_at),
                },
                "removed": {
                    "box_id": removed.id,
                    "box_barcode": removed.barcode,
                    "box_number": removed.box_number,
                    "lot_side": collision.removed_lot_side,
                    "status": removed.status.value,
                    "archived_at": _collision_timestamp(removed.archived_at),
                },
                "relinked_request_items": _bounded_merge_ids(collision_item_ids),
                "relinked_discrepancies": _bounded_merge_ids(
                    collision_discrepancy_ids
                ),
                "deleted_box_events": _bounded_merge_ids(collision_event_ids),
                "deleted_box_event_snapshots": [
                    _box_event_signature_value(event)
                    for event in box_events_by_box[removed.id][:_OVERLAP_LIST_LIMIT]
                ],
                "deleted_box_event_snapshots_truncated": (
                    len(box_events_by_box[removed.id]) > _OVERLAP_LIST_LIMIT
                ),
            }
        )

    source_request_items = [
        item for item in request_items if item.lot_id == source.id
    ]
    removed_id_set = set(removed_box_ids)
    moved_boxes = [
        box
        for box in boxes
        if box.lot_id == source.id and box.id not in removed_id_set
    ]
    final_pallet_id_by_box_id: dict[int, int | None] = {}
    for box in boxes:
        if box.id in removed_id_set:
            continue
        combined_target = (
            combined_pallet_targets.get(box.pallet_id)
            if box.pallet_id is not None
            else None
        )
        final_pallet_id_by_box_id[box.id] = (
            combined_target.id if combined_target is not None else box.pallet_id
        )
    for removed_box_id, survivor_box_id in removed_to_survivor.items():
        final_pallet_id_by_box_id[removed_box_id] = final_pallet_id_by_box_id.get(
            survivor_box_id
        )
    combined_box_ids: dict[int, list[int]] = {
        source_pallet_id: sorted(
            box.id
            for box in boxes
            if box.pallet_id == source_pallet_id and box.id not in removed_id_set
        )
        for source_pallet_id in combined_pallet_targets
    }
    original_pallet_box_ids: dict[int, list[int]] = {
        source_pallet_id: sorted(
            box.id for box in boxes if box.pallet_id == source_pallet_id
        )
        for source_pallet_id in combined_pallet_targets
    }
    relinked_item_ids = sorted(
        item.id
        for item in request_items
        if item.box_id in removed_to_survivor
    )
    relinked_discrepancy_ids = sorted(
        discrepancy.id
        for discrepancy in discrepancies
        if discrepancy.box_id in removed_to_survivor
    )
    survivor_box_ids = sorted(
        {collision.survivor_box_id for collision in execution_collisions}
    )
    request_warehouse_ids = set(
        db.scalars(
            select(BoxRequest.warehouse_id).where(
                BoxRequest.id.in_(affected_request_ids)
            )
        ).all()
        if affected_request_ids
        else []
    )
    warehouse_ids = sorted(
        {
            box.current_warehouse_id
            for box in boxes
            if box.archived_at is None
        }
        | set(lot_warehouse_ids(db, source.id))
        | set(lot_warehouse_ids(db, target.id))
        | request_warehouse_ids
    )
    source_name = source.name
    target_name = target.name
    source_version = source.version
    target_version = target.version
    now = datetime.now(UTC)
    pallet_actions_metadata = [
        {
            "action": "combine",
            "source_pallet": _pallet_signature_value(
                pallets_by_id[source_pallet_id]
            ),
            "target_pallet": _pallet_signature_value(target_pallet),
            "box_ids": original_pallet_box_ids[source_pallet_id],
            "surviving_reassigned_box_ids": combined_box_ids[source_pallet_id],
        }
        for source_pallet_id, target_pallet in sorted(
            combined_pallet_targets.items()
        )
    ] + [
        {
            "action": "transfer",
            "source_pallet": _pallet_signature_value(pallet),
            "from_lot_id": source.id,
            "to_lot_id": target.id,
            "box_ids": sorted(
                box.id for box in boxes if box.pallet_id == pallet.id
            ),
        }
        for pallet in sorted(transferred_pallets, key=lambda item: item.id)
    ]

    metadata = {
        "operation": "merge",
        "source_lot_id": source.id,
        "source_lot_barcode": source.barcode,
        "source_lot_name": source_name,
        "target_lot_id": target.id,
        "target_lot_barcode": target.barcode,
        "target_lot_name": target_name,
        "actor_user_id": user.id,
        "reason": cleaned_reason,
        "moved_box_count": len(moved_boxes),
        "moved_request_item_count": len(source_request_items),
        "expected_source_version": expected_source_version,
        "expected_target_version": expected_target_version,
        "source_version_before": source_version,
        "target_version_before": target_version,
        "overwrite_archived_collisions": overwrite_archived_collisions,
        "collision_signature": (
            candidate.collision_signature
            if overwrite_archived_collisions
            else None
        ),
        "overwrite_collisions": collision_snapshots[:_OVERLAP_LIST_LIMIT],
        "overwrite_collision_count": len(collision_snapshots),
        "overwrite_collisions_truncated": (
            len(collision_snapshots) > _OVERLAP_LIST_LIMIT
        ),
        "survivor_boxes": _bounded_merge_ids(survivor_box_ids),
        "removed_boxes": _bounded_merge_ids(removed_box_ids),
        "moved_files": _bounded_merge_ids([file.id for file in moved_files]),
        "removed_files": _bounded_merge_ids(file_delete_ids),
        "removed_file_snapshots": file_delete_snapshots[:_OVERLAP_LIST_LIMIT],
        "removed_file_snapshots_truncated": (
            len(file_delete_snapshots) > _OVERLAP_LIST_LIMIT
        ),
        "file_reference_collisions": [
            {
                "normalized_reference": collision.normalized_reference,
                "survivor_file_id": collision.survivor_file_id,
                "removed_file_ids": collision.removed_file_ids,
                "archived_only": collision.archived_only,
            }
            for collision in candidate.all_archived_file_collisions[
                :_OVERLAP_LIST_LIMIT
            ]
        ],
        "file_reference_collision_count": len(
            candidate.all_archived_file_collisions
        ),
        "deleted_file_events": _bounded_merge_ids(deleted_file_event_ids),
        "detached_request_file_snapshots": _bounded_merge_ids(
            detached_file_snapshot_ids
        ),
        "relinked_request_items": _bounded_merge_ids(relinked_item_ids),
        "relinked_discrepancies": _bounded_merge_ids(
            relinked_discrepancy_ids
        ),
        "deleted_box_events": _bounded_merge_ids(box_event_ids),
        "pallet_actions": pallet_actions_metadata[:_OVERLAP_LIST_LIMIT],
        "pallet_action_count": len(pallet_actions_metadata),
        "pallet_actions_truncated": (
            len(pallet_actions_metadata) > _OVERLAP_LIST_LIMIT
        ),
        # Preview-oriented lists above remain bounded, but identity evidence
        # is deliberately complete and survives every absorbed-row deletion.
        "barcode_evidence": {
            "lots": [
                {"id": source.id, "barcode": source.barcode, "side": "source"},
                {"id": target.id, "barcode": target.barcode, "side": "survivor"},
            ],
            "pallets": [
                {
                    "id": pallet.id,
                    "barcode": pallet.barcode,
                    "lot_id": pallet.lot_id,
                }
                for pallet in pallets
            ],
            "boxes": [
                {
                    "id": box.id,
                    "barcode": box.barcode,
                    "lot_id": box.lot_id,
                    "removed": box.id in removed_id_set,
                }
                for box in boxes
            ],
            "files": [
                {
                    "id": file.id,
                    "barcode": file.barcode,
                    "lot_id": file.lot_id,
                    "box_id": file.box_id,
                    "removed": file.id in file_delete_id_set,
                }
                for file in files
            ],
        },
        "absorbed_pallets": _bounded_merge_ids(
            list(combined_pallet_targets)
        ),
        "transferred_pallets": _bounded_merge_ids(
            [pallet.id for pallet in transferred_pallets]
        ),
        "totals": {
            "overwritten_archived_box_count": len(removed_box_ids),
            "relinked_request_item_count": len(relinked_item_ids),
            "relinked_discrepancy_count": len(relinked_discrepancy_ids),
            "deleted_box_event_count": len(box_event_ids),
            "moved_box_count": len(moved_boxes),
            "moved_request_item_count": len(source_request_items),
            "combined_pallet_count": len(combined_pallet_targets),
            "moved_pallet_count": len(transferred_pallets),
            "moved_file_count": len(moved_files),
            "overwritten_archived_file_count": len(file_delete_ids),
            "deleted_file_event_count": len(deleted_file_event_ids),
            "detached_request_file_snapshot_count": len(
                detached_file_snapshot_ids
            ),
        },
    }

    try:
        with db.begin_nested():
            _retire_lot_graph_barcodes(
                db,
                user=user,
                reason=cleaned_reason,
                operation="lot_merge_archived_collision_overwrite",
                boxes=[boxes_by_id[box_id] for box_id in removed_box_ids],
                files=[
                    file for file in files if file.id in file_delete_id_set
                ],
                metadata={
                    "source_lot_id": source.id,
                    "source_lot_name": source_name,
                    "target_lot_id": target.id,
                    "target_lot_name": target_name,
                    "collision_signature": candidate.collision_signature,
                    "overwrite_archived_collisions": True,
                },
            )
            if moved_files and db.get_bind().dialect.name == "postgresql":
                db.execute(
                    text(
                        "SET CONSTRAINTS fk_box_files_box_lot_boxes DEFERRED"
                    )
                )
            elif moved_files and db.get_bind().dialect.name == "sqlite":
                db.execute(text("PRAGMA defer_foreign_keys = ON"))
            for item in request_items:
                original_box_id = item.box_id
                survivor_id = removed_to_survivor.get(original_box_id)
                if survivor_id is not None:
                    item.box_id = survivor_id
                    item.pallet_id = final_pallet_id_by_box_id.get(original_box_id)
                elif item.pallet_id in combined_pallet_targets:
                    item.pallet_id = combined_pallet_targets[item.pallet_id].id
            for discrepancy in discrepancies:
                survivor_id = removed_to_survivor.get(discrepancy.box_id)
                if survivor_id is not None:
                    discrepancy.box_id = survivor_id
            if detached_file_snapshot_ids:
                db.execute(
                    update(BoxRequestItemFileSnapshot)
                    .where(
                        BoxRequestItemFileSnapshot.id.in_(
                            detached_file_snapshot_ids
                        )
                    )
                    .values(file_id=None)
                    .execution_options(synchronize_session="fetch")
                )
            if deleted_file_event_ids:
                db.execute(
                    delete(BoxFileEvent)
                    .where(BoxFileEvent.id.in_(deleted_file_event_ids))
                    .execution_options(synchronize_session="fetch")
                )
            if file_delete_ids:
                db.execute(
                    delete(BoxFile)
                    .where(BoxFile.id.in_(file_delete_ids))
                    .execution_options(synchronize_session="fetch")
                )
            if box_event_ids:
                db.execute(
                    delete(BoxEvent)
                    .where(BoxEvent.id.in_(box_event_ids))
                    .execution_options(synchronize_session="fetch")
                )
            for removed_box_id in removed_box_ids:
                db.delete(boxes_by_id[removed_box_id])
            # Free target-side unique box numbers before source survivors move.
            db.flush()

            for box in moved_boxes:
                box.lot_id = target.id
                combined_target = (
                    combined_pallet_targets.get(box.pallet_id)
                    if box.pallet_id is not None
                    else None
                )
                if combined_target is not None:
                    source_pallet_id = box.pallet_id
                    box.pallet_id = combined_target.id
                    db.add_all(
                        [
                            BoxEvent(
                                box_id=box.id,
                                warehouse_id=box.current_warehouse_id,
                                event_type=BoxEventType.pallet_unassigned,
                                from_status=box.status,
                                to_status=box.status,
                                from_warehouse_id=box.current_warehouse_id,
                                to_warehouse_id=box.current_warehouse_id,
                                occurred_at=now,
                                user_id=user.id,
                                note=cleaned_reason,
                                event_metadata={
                                    "operation": "lot_merge_pallet_combine",
                                    "from_pallet_id": source_pallet_id,
                                    "to_pallet_id": combined_target.id,
                                    "source_lot_id": source.id,
                                    "target_lot_id": target.id,
                                },
                            ),
                            BoxEvent(
                                box_id=box.id,
                                warehouse_id=box.current_warehouse_id,
                                event_type=BoxEventType.pallet_assigned,
                                from_status=box.status,
                                to_status=box.status,
                                from_warehouse_id=box.current_warehouse_id,
                                to_warehouse_id=box.current_warehouse_id,
                                occurred_at=now,
                                user_id=user.id,
                                note=cleaned_reason,
                                event_metadata={
                                    "operation": "lot_merge_pallet_combine",
                                    "from_pallet_id": source_pallet_id,
                                    "to_pallet_id": combined_target.id,
                                    "source_lot_id": source.id,
                                    "target_lot_id": target.id,
                                },
                            ),
                        ]
                    )
            for file in moved_files:
                before_file = {
                    "id": file.id,
                    "lot_id": source.id,
                    "lot": source_name,
                    "box_id": file.box_id,
                    "reference": file.reference,
                    "position": file.position,
                    "archived_at": _collision_timestamp(file.archived_at),
                }
                file.lot_id = target.id
                file.updated_by_user_id = user.id
                file.updated_at = now
                db.add(
                    BoxFileEvent(
                        file_id=file.id,
                        event_type=BoxFileEventType.lot_reassigned,
                        before_snapshot=before_file,
                        after_snapshot={
                            **before_file,
                            "lot_id": target.id,
                            "lot": target_name,
                        },
                        actor_user_id=user.id,
                        reason=cleaned_reason,
                        event_metadata={
                            "operation": "lot_merge",
                            "source_lot_id": source.id,
                            "target_lot_id": target.id,
                        },
                        occurred_at=now,
                    )
                )
            for item in source_request_items:
                # ``item.lot`` is an immutable historical text snapshot.
                item.lot_id = target.id

            for pallet in transferred_pallets:
                pallet.lot_id = target.id
                pallet.updated_by_user_id = user.id
                pallet.updated_at = now
                db.add(
                    PalletEvent(
                        pallet_id=pallet.id,
                        event_type=PalletEventType.lot_reassigned,
                        actor_user_id=user.id,
                        reason=cleaned_reason,
                        occurred_at=now,
                        event_metadata={
                            "operation": "lot_merge_pallet_transfer",
                            "from_lot_id": source.id,
                            "from_lot_name": source_name,
                            "to_lot_id": target.id,
                            "to_lot_name": target_name,
                            "box_ids": sorted(
                                box.id
                                for box in boxes
                                if box.pallet_id == pallet.id
                            ),
                        },
                    )
                )

            for source_pallet_id, target_pallet in sorted(
                combined_pallet_targets.items()
            ):
                source_pallet = pallets_by_id[source_pallet_id]
                reassigned_box_ids = combined_box_ids[source_pallet_id]
                target_pallet.updated_by_user_id = user.id
                target_pallet.updated_at = now
                source_pallet.is_active = False
                source_pallet.archived_at = now
                source_pallet.archived_by_user_id = user.id
                source_pallet.archive_reason = (
                    f"Absorbed into pallet {target_pallet.pallet_number!r} "
                    f"during lot merge: {cleaned_reason}"
                )
                source_pallet.absorbed_into_pallet_id = target_pallet.id
                source_pallet.absorbed_at = now
                source_pallet.absorbed_by_user_id = user.id
                source_pallet.updated_by_user_id = user.id
                source_pallet.updated_at = now
                if reassigned_box_ids:
                    db.add_all(
                        [
                            PalletEvent(
                                pallet_id=source_pallet.id,
                                event_type=PalletEventType.boxes_unassigned,
                                actor_user_id=user.id,
                                reason=cleaned_reason,
                                occurred_at=now,
                                event_metadata={
                                    "operation": "lot_merge_pallet_combine",
                                    "box_ids": reassigned_box_ids,
                                    "box_count": len(reassigned_box_ids),
                                    "to_pallet_id": target_pallet.id,
                                },
                            ),
                            PalletEvent(
                                pallet_id=target_pallet.id,
                                event_type=PalletEventType.boxes_assigned,
                                actor_user_id=user.id,
                                reason=cleaned_reason,
                                occurred_at=now,
                                event_metadata={
                                    "operation": "lot_merge_pallet_combine",
                                    "box_ids": reassigned_box_ids,
                                    "box_count": len(reassigned_box_ids),
                                    "from_pallet_id": source_pallet.id,
                                },
                            ),
                        ]
                    )
                db.add(
                    PalletEvent(
                        pallet_id=source_pallet.id,
                        event_type=PalletEventType.merged_absorbed,
                        old_pallet_number=source_pallet.pallet_number,
                        new_pallet_number=target_pallet.pallet_number,
                        actor_user_id=user.id,
                        reason=cleaned_reason,
                        occurred_at=now,
                        event_metadata={
                            "operation": "lot_merge_pallet_absorbed",
                            "source_lot_id": source.id,
                            "target_lot_id": target.id,
                            "target_pallet_id": target_pallet.id,
                            "all_source_box_ids": (
                                original_pallet_box_ids[source_pallet.id]
                            ),
                            "reassigned_box_ids": reassigned_box_ids,
                        },
                    )
                )

            source.normalized_name = None
            source.merged_into_lot_id = target.id
            source.merged_at = now
            source.merged_by_user_id = user.id
            source.updated_by_user_id = user.id
            target.updated_by_user_id = user.id
            target.version = target.version + 1

            db.add_all(
                [
                    LotEvent(
                        lot_id=source.id,
                        event_type=LotEventType.merged,
                        old_name=source_name,
                        new_name=target_name,
                        actor_user_id=user.id,
                        reason=cleaned_reason,
                        event_metadata={**metadata, "event_side": "source"},
                    ),
                    LotEvent(
                        lot_id=target.id,
                        event_type=LotEventType.merged,
                        old_name=source_name,
                        new_name=target_name,
                        actor_user_id=user.id,
                        reason=cleaned_reason,
                        event_metadata={**metadata, "event_side": "target"},
                    ),
                ]
            )
            db.add_all(
                [
                    BoxEvent(
                        box_id=box.id,
                        warehouse_id=box.current_warehouse_id,
                        event_type=BoxEventType.lot_reassigned,
                        from_status=box.status,
                        to_status=box.status,
                        from_warehouse_id=box.current_warehouse_id,
                        to_warehouse_id=box.current_warehouse_id,
                        occurred_at=now,
                        user_id=user.id,
                        note=cleaned_reason,
                        event_metadata={
                            **metadata,
                            "operation": "lot_merge",
                            "box_id": box.id,
                            "box_number": box.box_number,
                            "box_was_archived": box.archived_at is not None,
                        },
                    )
                    for box in moved_boxes
                ]
            )
            db.flush()
    except IntegrityError as exc:
        refreshed_candidate = _merge_candidate(db, source, target)
        if commit:
            db.rollback()
        raise LotMergeConflictError(
            "merge conflicted with a concurrent inventory change",
            code="merge_integrity_conflict",
            source=source,
            target=target,
            candidate=refreshed_candidate,
        ) from exc
    except Exception:
        if commit:
            db.rollback()
        raise

    if commit:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(source)
        db.refresh(target)
    return LotMergeResult(
        source=source,
        target=target,
        moved_box_count=len(moved_boxes),
        moved_request_item_count=len(source_request_items),
        warehouse_ids=warehouse_ids,
        overwritten_archived_box_count=len(removed_box_ids),
        relinked_request_item_count=len(relinked_item_ids),
        relinked_discrepancy_count=len(relinked_discrepancy_ids),
        deleted_box_event_count=len(box_event_ids),
        combined_pallet_count=len(combined_pallet_targets),
        moved_pallet_count=len(transferred_pallets),
        absorbed_pallet_ids=sorted(combined_pallet_targets),
        moved_pallet_ids=sorted(pallet.id for pallet in transferred_pallets),
        survivor_box_ids=survivor_box_ids,
        removed_box_ids=removed_box_ids,
        moved_file_count=len(moved_files),
        overwritten_archived_file_count=len(file_delete_ids),
        deleted_file_event_count=len(deleted_file_event_ids),
    )


def _scoped_sources(
    user: User,
    *,
    warehouse_id: int | None = None,
):
    """Build the ACL-filtered physical and staged lot sources.

    ACL predicates are applied here, before every aggregate. This is the
    security boundary shared by list, detail, picker, and exports.
    """
    allowed = allowed_warehouse_ids(user)
    box_conditions = [Box.archived_at.is_(None)]
    staged_conditions = [
        BoxRequest.status == BoxRequestStatus.submitted,
        BoxRequest.origin.in_(
            (BoxRequestOrigin.xlsx_import, BoxRequestOrigin.manual_entry)
        ),
        BoxRequestItem.lot_id.is_not(None),
    ]
    if warehouse_id is not None:
        box_conditions.append(Box.current_warehouse_id == warehouse_id)
        staged_conditions.append(BoxRequest.warehouse_id == warehouse_id)
    if allowed is not None:
        box_conditions.append(Box.current_warehouse_id.in_(allowed))
        staged_conditions.append(BoxRequest.warehouse_id.in_(allowed))

    boxes = (
        select(
            Box.id.label("box_id"),
            Box.lot_id.label("lot_id"),
            Box.current_warehouse_id.label("warehouse_id"),
            Box.status.label("status"),
            Box.updated_at.label("updated_at"),
        )
        .where(*box_conditions)
        .cte("scoped_lot_boxes")
    )
    staged = (
        select(
            BoxRequestItem.lot_id.label("lot_id"),
            BoxRequest.warehouse_id.label("warehouse_id"),
            BoxRequest.id.label("request_id"),
        )
        .join(BoxRequestItem, BoxRequestItem.request_id == BoxRequest.id)
        .where(*staged_conditions)
        .cte("scoped_lot_staged")
    )
    represented = union_all(
        select(boxes.c.lot_id),
        select(staged.c.lot_id),
    ).cte("represented_lots")
    warehouses = union_all(
        select(boxes.c.lot_id, boxes.c.warehouse_id),
        select(staged.c.lot_id, staged.c.warehouse_id),
    ).cte("scoped_lot_warehouses")
    return boxes, staged, represented, warehouses


def _count_if(condition):
    return func.sum(case((condition, 1), else_=0))


def _progress_state(eligible: int, completed: int) -> LotProgressState:
    if eligible == 0:
        return "no_eligible"
    if completed == eligible:
        return "complete"
    if completed == 0:
        return "active"
    return "in_progress"


def _summary_statement(
    user: User,
    *,
    search: str | None,
    warehouse_id: int | None,
    progress_state: LotProgressState | None,
    lot_id: int | None,
    sort_by: LotSortField,
    sort_dir: SortDirection,
    allow_unrepresented_lot: bool,
):
    boxes, staged, represented, warehouses = _scoped_sources(
        user, warehouse_id=warehouse_id
    )
    box_aggregate = (
        select(
            boxes.c.lot_id,
            func.count().label("box_count"),
            _count_if(boxes.c.status.in_(ACTIVE_STATUSES)).label(
                "physical_box_count"
            ),
            *[
                _count_if(boxes.c.status == status).label(f"{status.value}_count")
                for status in BoxStatus
            ],
            _count_if(boxes.c.status != BoxStatus.quarantined).label(
                "eligible_box_count"
            ),
            _count_if(
                boxes.c.status.in_(
                    (
                        BoxStatus.incomplete,
                        BoxStatus.ready_to_return,
                        BoxStatus.returned,
                    )
                )
            ).label("completed_box_count"),
            func.max(boxes.c.updated_at).label("last_box_activity"),
        )
        .group_by(boxes.c.lot_id)
        .cte("lot_box_aggregate")
    )
    file_aggregate = (
        select(
            boxes.c.lot_id,
            _count_if(BoxFile.archived_at.is_(None)).label("active_file_count"),
            _count_if(BoxFile.archived_at.is_not(None)).label(
                "archived_file_count"
            ),
        )
        .join(BoxFile, BoxFile.box_id == boxes.c.box_id)
        .group_by(boxes.c.lot_id)
        .cte("lot_file_aggregate")
    )
    staged_aggregate = (
        select(
            staged.c.lot_id,
            func.count(func.distinct(staged.c.request_id)).label(
                "staged_receipt_count"
            ),
        )
        .group_by(staged.c.lot_id)
        .cte("lot_staged_aggregate")
    )
    warehouse_aggregate = (
        select(
            warehouses.c.lot_id,
            func.count(func.distinct(warehouses.c.warehouse_id)).label(
                "warehouse_count"
            ),
        )
        .group_by(warehouses.c.lot_id)
        .cte("lot_warehouse_aggregate")
    )

    eligible = func.coalesce(box_aggregate.c.eligible_box_count, 0)
    completed = func.coalesce(box_aggregate.c.completed_box_count, 0)
    box_count = func.coalesce(box_aggregate.c.box_count, 0)
    completion = case(
        (eligible == 0, literal(None)),
        else_=cast(completed * 100.0, Float) / cast(eligible, Float),
    ).label("completion_percent")

    stmt = (
        select(
            Lot,
            box_count.label("box_count"),
            func.coalesce(box_aggregate.c.physical_box_count, 0).label(
                "physical_box_count"
            ),
            func.coalesce(file_aggregate.c.active_file_count, 0).label(
                "active_file_count"
            ),
            func.coalesce(file_aggregate.c.archived_file_count, 0).label(
                "archived_file_count"
            ),
            *[
                func.coalesce(
                    getattr(box_aggregate.c, f"{status.value}_count"), 0
                ).label(f"{status.value}_count")
                for status in BoxStatus
            ],
            eligible.label("eligible_box_count"),
            completed.label("completed_box_count"),
            completion,
            func.coalesce(warehouse_aggregate.c.warehouse_count, 0).label(
                "warehouse_count"
            ),
            func.coalesce(staged_aggregate.c.staged_receipt_count, 0).label(
                "staged_receipt_count"
            ),
            box_aggregate.c.last_box_activity,
        )
        .outerjoin(box_aggregate, box_aggregate.c.lot_id == Lot.id)
        .outerjoin(file_aggregate, file_aggregate.c.lot_id == Lot.id)
        .outerjoin(staged_aggregate, staged_aggregate.c.lot_id == Lot.id)
        .outerjoin(warehouse_aggregate, warehouse_aggregate.c.lot_id == Lot.id)
        .where(Lot.merged_into_lot_id.is_(None))
    )
    if (
        (user.role != UserRole.admin or warehouse_id is not None)
        and not allow_unrepresented_lot
    ):
        stmt = stmt.where(Lot.id.in_(select(represented.c.lot_id)))
    if lot_id is not None:
        stmt = stmt.where(Lot.id == lot_id)
    if search and search.strip():
        needle = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Lot.name.ilike(needle),
                Lot.barcode_identity.has(BarcodeIdentity.barcode.ilike(needle)),
            )
        )
    if progress_state == "no_eligible":
        stmt = stmt.where(eligible == 0)
    elif progress_state == "complete":
        stmt = stmt.where(eligible > 0, completed == eligible)
    elif progress_state == "active":
        stmt = stmt.where(eligible > 0, completed == 0)
    elif progress_state == "in_progress":
        stmt = stmt.where(completed > 0, completed < eligible)

    sort_column = {
        "name": func.lower(Lot.name),
        "completion": completion,
        "box_count": box_count,
        "last_activity": box_aggregate.c.last_box_activity,
    }[sort_by]
    ordered = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
    stmt = stmt.order_by(ordered.nulls_last(), Lot.id.asc())
    return stmt, warehouses


def _warehouse_names_for(
    db: Session,
    *,
    lot_ids: list[int],
    warehouses,
) -> dict[int, list[str]]:
    names: dict[int, list[str]] = {lot_id: [] for lot_id in lot_ids}
    if not lot_ids:
        return names
    rows = db.execute(
        select(warehouses.c.lot_id, Warehouse.name)
        .join(Warehouse, Warehouse.id == warehouses.c.warehouse_id)
        .where(warehouses.c.lot_id.in_(lot_ids))
        .distinct()
        .order_by(warehouses.c.lot_id, Warehouse.name)
    ).all()
    for row_lot_id, warehouse_name in rows:
        names[int(row_lot_id)].append(warehouse_name)
    return names


def list_lot_summaries(
    db: Session,
    *,
    user: User,
    search: str | None = None,
    warehouse_id: int | None = None,
    progress_state: LotProgressState | None = None,
    lot_id: int | None = None,
    sort_by: LotSortField = "last_activity",
    sort_dir: SortDirection = "desc",
    page: int = 1,
    page_size: int = 50,
    allow_unrepresented_lot: bool = False,
) -> tuple[list[LotSummary], int]:
    """Return ACL-safe lot progress using a constant number of set queries."""
    stmt, warehouses = _summary_statement(
        user,
        search=search,
        warehouse_id=warehouse_id,
        progress_state=progress_state,
        lot_id=lot_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        allow_unrepresented_lot=allow_unrepresented_lot,
    )
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).all()
    lot_ids = [row[0].id for row in rows]
    warehouse_names = _warehouse_names_for(
        db, lot_ids=lot_ids, warehouses=warehouses
    )
    acl_scoped = user.role != UserRole.admin
    summaries: list[LotSummary] = []
    for row in rows:
        lot = row[0]
        values = row._mapping
        eligible = int(values["eligible_box_count"])
        completed = int(values["completed_box_count"])
        raw_percent = values["completion_percent"]
        summaries.append(
            LotSummary(
                id=lot.id,
                barcode=lot.barcode,
                name=lot.name,
                normalized_name=lot.normalized_name,
                version=lot.version,
                created_at=lot.created_at,
                updated_at=lot.updated_at,
                physical_box_count=int(values["physical_box_count"]),
                box_count=int(values["box_count"]),
                active_file_count=int(values["active_file_count"]),
                archived_file_count=int(values["archived_file_count"]),
                status_counts={
                    status.value: int(values[f"{status.value}_count"])
                    for status in BoxStatus
                },
                eligible_box_count=eligible,
                completed_box_count=completed,
                completion_percent=(
                    round(float(raw_percent), 2) if raw_percent is not None else None
                ),
                progress_state=_progress_state(eligible, completed),
                warehouse_count=int(values["warehouse_count"]),
                warehouse_names=warehouse_names[lot.id],
                staged_receipt_count=int(values["staged_receipt_count"]),
                last_box_activity=values["last_box_activity"],
                acl_scoped=acl_scoped,
                scope_label=(
                    "accessible_warehouses_only" if acl_scoped else "global"
                ),
            )
        )
    return summaries, total


def get_visible_lot_summary(
    db: Session,
    *,
    user: User,
    lot_id: int,
    allow_unrepresented: bool = False,
) -> LotSummary:
    rows, _ = list_lot_summaries(
        db,
        user=user,
        lot_id=lot_id,
        page=1,
        page_size=1,
        allow_unrepresented_lot=allow_unrepresented,
    )
    if not rows:
        raise LotNotFoundError(f"lot {lot_id} not found")
    return rows[0]


def list_lot_options(
    db: Session,
    *,
    user: User,
    search: str | None = None,
    page: int = 1,
    limit: int = 25,
) -> tuple[list[LotOption], int]:
    """Return lightweight identities without computing progress aggregates."""
    _boxes, _staged, represented, _warehouses = _scoped_sources(user)
    stmt = select(Lot).where(Lot.merged_into_lot_id.is_(None))
    if user.role != UserRole.admin:
        stmt = stmt.where(Lot.id.in_(select(represented.c.lot_id)))
    exact_normalized: str | None = None
    if search and search.strip():
        cleaned = validate_lot_name(search)
        exact_normalized = normalize_lot_name(cleaned)
        stmt = stmt.where(
            or_(
                Lot.name.ilike(f"%{cleaned}%"),
                Lot.normalized_name == exact_normalized,
                Lot.barcode_identity.has(
                    BarcodeIdentity.barcode.ilike(f"%{cleaned}%")
                ),
            )
        )
        stmt = stmt.order_by(
            case((Lot.normalized_name == exact_normalized, 0), else_=1),
            func.lower(Lot.name),
            Lot.id,
        )
    else:
        stmt = stmt.order_by(func.lower(Lot.name), Lot.id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    lots = db.scalars(stmt.offset((page - 1) * limit).limit(limit)).all()
    return (
        [
            LotOption(
                id=lot.id,
                barcode=lot.barcode,
                name=lot.name,
                normalized_name=lot.normalized_name,
                exact_normalized_match=lot.normalized_name == exact_normalized,
            )
            for lot in lots
        ],
        total,
    )


def lot_warehouse_ids(db: Session, lot_id: int) -> list[int]:
    """Warehouses currently representing a lot, for per-warehouse SSE fanout."""
    box_ids = select(Box.current_warehouse_id).where(
        Box.lot_id == lot_id,
        Box.archived_at.is_(None),
    )
    staged_ids = (
        select(BoxRequest.warehouse_id)
        .join(BoxRequestItem, BoxRequestItem.request_id == BoxRequest.id)
        .where(
            BoxRequestItem.lot_id == lot_id,
            BoxRequest.status == BoxRequestStatus.submitted,
            BoxRequest.origin.in_(
                (BoxRequestOrigin.xlsx_import, BoxRequestOrigin.manual_entry)
            ),
        )
    )
    return sorted(
        {
            int(warehouse_id)
            for warehouse_id in db.scalars(union_all(box_ids, staged_ids)).all()
        }
    )


__all__ = [
    "MAX_LOT_NAME_LENGTH",
    "LotAccessError",
    "LotConflictError",
    "LotMergeCandidate",
    "LotMergeConflictError",
    "LotMergeResult",
    "LotNameCollisionError",
    "LotOption",
    "LotNotFoundError",
    "LotForcePurgeBlocker",
    "LotForcePurgeImpactPlan",
    "LotForcePurgeItemPosition",
    "LotForcePurgeLineageDetach",
    "LotForcePurgeObjectCleanupPlan",
    "LotForcePurgeRequestRewrite",
    "LotPurgeAnalysisError",
    "LotPurgeBlocker",
    "LotPurgeCleanupResult",
    "LotPurgeConflictError",
    "LotPurgeEligibility",
    "LotPurgeEntity",
    "LotPurgeNotFoundError",
    "LotPurgeRequestPreview",
    "LotPurgeResult",
    "LotRuleError",
    "LotSummary",
    "LotVersionConflictError",
    "analyze_lot_force_purge_impact",
    "analyze_lot_purge_eligibility",
    "cleanup_lot_purge_objects",
    "find_lot",
    "get_lot",
    "get_or_create_lot",
    "get_or_create_lot_result",
    "get_visible_lot_summary",
    "list_lot_options",
    "list_lot_summaries",
    "lock_active_lots_for_use",
    "lock_lots_exclusively",
    "lot_warehouse_ids",
    "merge_lots",
    "normalize_lot_name",
    "purge_lot",
    "rename_lot",
    "resolve_lot",
    "resolve_lot_names_for_use",
    "validate_lot_name",
]
