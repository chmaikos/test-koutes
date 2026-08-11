"""Domain operations for first-class lot identities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Float, case, cast, func, literal, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
    clean_lot_name,
    normalize_lot_name,
)
from app.models.requests import (
    BoxRequest,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.acl import allowed_warehouse_ids, can_access


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


@dataclass(frozen=True)
class LotMergeCandidate:
    source_id: int
    source_name: str
    source_version: int
    target_id: int
    target_name: str
    target_version: int
    merge_allowed: bool
    overlapping_box_numbers: list[str]
    overlapping_box_count: int
    overlap_list_truncated: bool


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


LotProgressState = Literal["active", "in_progress", "complete", "no_eligible"]
LotSortField = Literal["name", "completion", "box_count", "last_activity"]
SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class LotSummary:
    id: int
    name: str
    normalized_name: str
    version: int
    created_at: datetime
    updated_at: datetime
    physical_box_count: int
    box_count: int
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
    name: str
    normalized_name: str
    exact_normalized_match: bool


def validate_lot_name(value: str) -> str:
    """Return the sole canonical display form used by backend workflows."""
    try:
        return clean_lot_name(value)
    except (AttributeError, TypeError, ValueError) as exc:
        message = str(exc) or "lot is required"
        raise LotRuleError(message) from exc


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


_OVERLAP_LIST_LIMIT = 100


def _overlapping_box_numbers(
    db: Session,
    source_id: int,
    target_id: int,
) -> tuple[list[str], int]:
    """Return a bounded sorted overlap across active and archived boxes."""
    overlap = (
        select(Box.box_number)
        .where(Box.lot_id.in_((source_id, target_id)))
        .group_by(Box.box_number)
        .having(func.count(func.distinct(Box.lot_id)) == 2)
        .order_by(Box.box_number)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(overlap)) or 0)
    numbers = list(
        db.scalars(select(overlap.c.box_number).limit(_OVERLAP_LIST_LIMIT)).all()
    )
    return numbers, total


def _merge_candidate(
    db: Session,
    source: Lot,
    target: Lot,
) -> LotMergeCandidate:
    overlaps, total = _overlapping_box_numbers(db, source.id, target.id)
    return LotMergeCandidate(
        source_id=source.id,
        source_name=source.name,
        source_version=source.version,
        target_id=target.id,
        target_name=target.name,
        target_version=target.version,
        merge_allowed=total == 0,
        overlapping_box_numbers=overlaps,
        overlapping_box_count=total,
        overlap_list_truncated=total > len(overlaps),
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


def merge_lots(
    db: Session,
    *,
    user: User,
    source_lot_id: int,
    target_lot_id: int,
    reason: str,
    expected_source_version: int,
    expected_target_version: int,
    commit: bool = True,
) -> LotMergeResult:
    """Merge source into target after locking and revalidating every identity."""
    if user.role != UserRole.admin:
        raise LotAccessError("lot merge requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise LotRuleError("a reason is required to merge lots")
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
    if source.version != expected_source_version:
        raise LotMergeConflictError(
            (
                "source lot version conflict: expected "
                f"{expected_source_version}, current {source.version}"
            ),
            code="source_version_conflict",
            source=source,
            target=target,
            candidate=_merge_candidate(db, source, target),
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
            candidate=_merge_candidate(db, source, target),
        )

    boxes = list(
        db.scalars(
            select(Box)
            .where(Box.lot_id.in_(ordered_ids))
            .order_by(Box.id)
            .with_for_update()
        ).all()
    )
    candidate = _merge_candidate(db, source, target)
    if not candidate.merge_allowed:
        raise LotMergeConflictError(
            "lots have overlapping physical box numbers",
            code="box_number_overlap",
            source=source,
            target=target,
            candidate=candidate,
        )

    request_items = list(
        db.scalars(
            select(BoxRequestItem)
            .where(BoxRequestItem.lot_id == source.id)
            .order_by(BoxRequestItem.id)
            .with_for_update()
        ).all()
    )
    request_warehouse_ids = set(
        db.scalars(
            select(BoxRequest.warehouse_id)
            .join(BoxRequestItem, BoxRequestItem.request_id == BoxRequest.id)
            .where(BoxRequestItem.lot_id == source.id)
            .distinct()
        ).all()
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
    moved_boxes = [box for box in boxes if box.lot_id == source.id]

    for box in moved_boxes:
        box.lot_id = target.id
    for item in request_items:
        # ``item.lot`` is an immutable historical text snapshot.
        item.lot_id = target.id

    now = datetime.now(UTC)
    source.normalized_name = None
    source.merged_into_lot_id = target.id
    source.merged_at = now
    source.merged_by_user_id = user.id
    source.updated_by_user_id = user.id
    target.updated_by_user_id = user.id
    # A merge changes the target identity even when the actor was already its
    # last updater; explicitly advance the optimistic-lock version.
    target.version = target.version + 1

    metadata = {
        "operation": "merge",
        "source_lot_id": source.id,
        "source_lot_name": source_name,
        "target_lot_id": target.id,
        "target_lot_name": target_name,
        "actor_user_id": user.id,
        "moved_box_count": len(moved_boxes),
        "moved_request_item_count": len(request_items),
        "expected_source_version": expected_source_version,
        "expected_target_version": expected_target_version,
        "source_version_before": source_version,
        "target_version_before": target_version,
    }
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
    try:
        db.flush()
    except IntegrityError as exc:
        raise LotMergeConflictError(
            "merge conflicted with a concurrent inventory change",
            code="merge_integrity_conflict",
            source=source,
            target=target,
        ) from exc
    if commit:
        db.commit()
        db.refresh(source)
        db.refresh(target)
    return LotMergeResult(
        source=source,
        target=target,
        moved_box_count=len(moved_boxes),
        moved_request_item_count=len(request_items),
        warehouse_ids=warehouse_ids,
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
        stmt = stmt.where(Lot.name.ilike(f"%{search.strip()}%"))
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
                name=lot.name,
                normalized_name=lot.normalized_name,
                version=lot.version,
                created_at=lot.created_at,
                updated_at=lot.updated_at,
                physical_box_count=int(values["physical_box_count"]),
                box_count=int(values["box_count"]),
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
    "LotRuleError",
    "LotSummary",
    "LotVersionConflictError",
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
    "rename_lot",
    "resolve_lot",
    "resolve_lot_names_for_use",
    "validate_lot_name",
]
