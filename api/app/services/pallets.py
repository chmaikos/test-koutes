from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Float, case, cast, func, or_, select, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.boxes import ACTIVE_STATUSES, Box, BoxStatus
from app.models.lots import Lot
from app.models.pallets import (
    Pallet,
    PalletEvent,
    PalletEventType,
    clean_pallet_number,
    normalize_pallet_number,
)
from app.models.requests import BoxRequest, BoxRequestItem, BoxRequestOrigin, BoxRequestStatus
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.acl import allowed_warehouse_ids, can_access
from app.services.boxes import set_box_pallet_assignment

PalletProgressState = Literal["active", "in_progress", "complete", "no_eligible"]
PalletSortField = Literal["pallet_number", "completion", "box_count", "latest_activity"]
SortDirection = Literal["asc", "desc"]


class PalletRuleError(ValueError):
    """Base class for pallet domain errors."""


class PalletAccessError(PalletRuleError):
    pass


class PalletNotFoundError(PalletRuleError):
    pass


class PalletConflictError(PalletRuleError):
    code = "pallet_conflict"


class PalletNumberCollisionError(PalletConflictError):
    code = "pallet_number_collision"


class PalletVersionConflictError(PalletConflictError):
    code = "version_conflict"


class PalletArchiveConflictError(PalletConflictError):
    code = "pallet_not_empty"

    def __init__(self, message: str, *, box_count: int):
        super().__init__(message)
        self.box_count = box_count


@dataclass(frozen=True)
class PalletSummary:
    id: int
    lot_id: int
    lot_name: str
    warehouse_ids: list[int]
    warehouse_names: list[str]
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
    status_counts: dict[str, int]
    eligible_box_count: int
    completed_box_count: int
    completion_percent: float | None
    progress_state: PalletProgressState
    latest_activity: datetime


@dataclass(frozen=True)
class PalletOption:
    id: int
    pallet_number: str
    normalized_pallet_number: str
    lot_id: int
    lot_name: str
    warehouse_ids: list[int]
    warehouse_names: list[str]
    is_active: bool
    exact_normalized_match: bool


@dataclass(frozen=True)
class PalletBoxMutationResult:
    pallet_id: int
    updated_box_ids: list[int]
    skipped: list[dict[str, object]]
    cancelled_request_ids: list[int]
    affected_pallet_warehouse_ids: dict[int, set[int]]


@dataclass(frozen=True)
class PalletEventView:
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
    event_metadata: dict[str, object]


def _count_if(condition):
    return func.sum(case((condition, 1), else_=0))


def _progress_state(eligible: int, completed: int) -> PalletProgressState:
    if eligible == 0:
        return "no_eligible"
    if completed == eligible:
        return "complete"
    if completed == 0:
        return "active"
    return "in_progress"


def _validate_mutable_parents(db: Session, pallet: Pallet) -> None:
    lot = db.get(Lot, pallet.lot_id)
    if lot is None or lot.merged_into_lot_id is not None:
        raise PalletConflictError("pallet lot is unavailable or merged")


def _visibility_sources(user: User):
    """Return ACL-scoped assigned boxes and represented lot identities."""
    allowed = allowed_warehouse_ids(user)
    assigned_conditions = [
        Box.pallet_id.is_not(None),
        Box.archived_at.is_(None),
    ]
    lot_box_conditions = [Box.archived_at.is_(None)]
    staged_conditions = [
        BoxRequest.status == BoxRequestStatus.submitted,
        BoxRequest.origin.in_(
            (BoxRequestOrigin.xlsx_import, BoxRequestOrigin.manual_entry)
        ),
        BoxRequestItem.lot_id.is_not(None),
    ]
    if allowed is not None:
        assigned_conditions.append(Box.current_warehouse_id.in_(allowed))
        lot_box_conditions.append(Box.current_warehouse_id.in_(allowed))
        staged_conditions.append(BoxRequest.warehouse_id.in_(allowed))

    assigned = (
        select(
            Box.pallet_id.label("pallet_id"),
            Box.current_warehouse_id.label("warehouse_id"),
            Box.status.label("status"),
            Box.updated_at.label("updated_at"),
        )
        .where(*assigned_conditions)
        .cte("scoped_pallet_boxes")
    )
    represented = union_all(
        select(Box.lot_id.label("lot_id")).where(*lot_box_conditions),
        select(BoxRequestItem.lot_id.label("lot_id"))
        .join(BoxRequest, BoxRequest.id == BoxRequestItem.request_id)
        .where(*staged_conditions),
    ).cte("represented_pallet_lots")
    return assigned, represented


def _warehouse_values_for(
    db: Session,
    *,
    pallet_ids: list[int],
    scoped_boxes,
) -> dict[int, tuple[list[int], list[str]]]:
    values = {pallet_id: ([], []) for pallet_id in pallet_ids}
    if not pallet_ids:
        return values
    rows = db.execute(
        select(
            scoped_boxes.c.pallet_id,
            scoped_boxes.c.warehouse_id,
            Warehouse.name,
        )
        .join(Warehouse, Warehouse.id == scoped_boxes.c.warehouse_id)
        .where(scoped_boxes.c.pallet_id.in_(pallet_ids))
        .distinct()
        .order_by(scoped_boxes.c.pallet_id, scoped_boxes.c.warehouse_id)
    ).all()
    for pallet_id, warehouse_id, warehouse_name in rows:
        ids, names = values[int(pallet_id)]
        ids.append(int(warehouse_id))
        names.append(str(warehouse_name))
    return values


def _visible_pallet(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    include_inactive: bool = False,
    lock: bool = False,
) -> Pallet:
    if include_inactive and user.role != UserRole.admin:
        raise PalletAccessError("inactive pallets require admin role")
    stmt = select(Pallet).where(Pallet.id == pallet_id)
    if not include_inactive:
        stmt = stmt.where(Pallet.is_active.is_(True))
    if user.role != UserRole.admin:
        _scoped_boxes, represented = _visibility_sources(user)
        stmt = stmt.where(Pallet.lot_id.in_(select(represented.c.lot_id)))
    if lock:
        stmt = stmt.with_for_update()
    pallet = db.scalar(stmt)
    if pallet is None:
        raise PalletNotFoundError(f"pallet {pallet_id} not found")
    return pallet


def list_pallet_summaries(
    db: Session,
    *,
    user: User,
    search: str | None = None,
    warehouse_id: int | None = None,
    lot_id: int | None = None,
    progress_state: PalletProgressState | None = None,
    include_inactive: bool = False,
    sort_by: PalletSortField = "latest_activity",
    sort_dir: SortDirection = "desc",
    page: int = 1,
    page_size: int = 50,
    pallet_id: int | None = None,
    allow_unrepresented_lot: bool = False,
) -> tuple[list[PalletSummary], int]:
    if include_inactive and user.role != UserRole.admin:
        raise PalletAccessError("inactive pallets require admin role")

    scoped_boxes, represented = _visibility_sources(user)
    aggregate = (
        select(
            scoped_boxes.c.pallet_id,
            func.count().label("box_count"),
            _count_if(scoped_boxes.c.status.in_(ACTIVE_STATUSES)).label(
                "physical_box_count"
            ),
            *[
                _count_if(scoped_boxes.c.status == status).label(
                    f"{status.value}_count"
                )
                for status in BoxStatus
            ],
            _count_if(scoped_boxes.c.status != BoxStatus.quarantined).label(
                "eligible_box_count"
            ),
            _count_if(
                scoped_boxes.c.status.in_(
                    (
                        BoxStatus.incomplete,
                        BoxStatus.ready_to_return,
                        BoxStatus.returned,
                    )
                )
            ).label("completed_box_count"),
            func.max(scoped_boxes.c.updated_at).label("last_box_activity"),
        )
        .group_by(scoped_boxes.c.pallet_id)
        .cte("pallet_box_aggregate")
    )
    eligible = func.coalesce(aggregate.c.eligible_box_count, 0)
    completed = func.coalesce(aggregate.c.completed_box_count, 0)
    box_count = func.coalesce(aggregate.c.box_count, 0)
    completion = case(
        (eligible == 0, None),
        else_=cast(completed * 100.0, Float) / cast(eligible, Float),
    ).label("completion_percent")
    latest_activity = case(
        (aggregate.c.last_box_activity.is_(None), Pallet.updated_at),
        (Pallet.updated_at >= aggregate.c.last_box_activity, Pallet.updated_at),
        else_=aggregate.c.last_box_activity,
    ).label("latest_activity")
    stmt = (
        select(
            Pallet,
            Lot.name.label("lot_name"),
            box_count.label("box_count"),
            func.coalesce(aggregate.c.physical_box_count, 0).label(
                "physical_box_count"
            ),
            *[
                func.coalesce(
                    getattr(aggregate.c, f"{status.value}_count"), 0
                ).label(f"{status.value}_count")
                for status in BoxStatus
            ],
            eligible.label("eligible_box_count"),
            completed.label("completed_box_count"),
            completion,
            latest_activity,
        )
        .join(Lot, Lot.id == Pallet.lot_id)
        .outerjoin(aggregate, aggregate.c.pallet_id == Pallet.id)
    )
    if not include_inactive:
        stmt = stmt.where(
            Pallet.is_active.is_(True),
            Lot.merged_into_lot_id.is_(None),
        )
    if user.role != UserRole.admin and not allow_unrepresented_lot:
        stmt = stmt.where(Pallet.lot_id.in_(select(represented.c.lot_id)))
    if warehouse_id is not None:
        stmt = stmt.where(
            Pallet.id.in_(
                select(scoped_boxes.c.pallet_id).where(
                    scoped_boxes.c.warehouse_id == warehouse_id
                )
            )
        )
    if lot_id is not None:
        stmt = stmt.where(Pallet.lot_id == lot_id)
    if pallet_id is not None:
        stmt = stmt.where(Pallet.id == pallet_id)
    if search and search.strip():
        cleaned_search = " ".join(search.split())
        stmt = stmt.where(
            or_(
                Pallet.pallet_number.ilike(f"%{cleaned_search}%"),
                Lot.name.ilike(f"%{cleaned_search}%"),
                Pallet.id.in_(
                    select(scoped_boxes.c.pallet_id)
                    .join(Warehouse, Warehouse.id == scoped_boxes.c.warehouse_id)
                    .where(Warehouse.name.ilike(f"%{cleaned_search}%"))
                ),
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
        "pallet_number": func.lower(Pallet.pallet_number),
        "completion": completion,
        "box_count": box_count,
        "latest_activity": latest_activity,
    }[sort_by]
    ordered = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
    stmt = stmt.order_by(ordered.nulls_last(), Pallet.id.asc())

    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(
        stmt.offset((page - 1) * page_size).limit(page_size)
    ).all()
    warehouse_values = _warehouse_values_for(
        db,
        pallet_ids=[row[0].id for row in rows],
        scoped_boxes=scoped_boxes,
    )
    summaries: list[PalletSummary] = []
    for row in rows:
        pallet = row[0]
        values = row._mapping
        eligible_count = int(values["eligible_box_count"])
        completed_count = int(values["completed_box_count"])
        raw_percent = values["completion_percent"]
        summaries.append(
            PalletSummary(
                id=pallet.id,
                lot_id=pallet.lot_id,
                lot_name=values["lot_name"],
                warehouse_ids=warehouse_values[pallet.id][0],
                warehouse_names=warehouse_values[pallet.id][1],
                pallet_number=pallet.pallet_number,
                normalized_pallet_number=pallet.normalized_pallet_number,
                version=pallet.version,
                is_active=pallet.is_active,
                archived_at=pallet.archived_at,
                archived_by_user_id=pallet.archived_by_user_id,
                archive_reason=pallet.archive_reason,
                absorbed_into_pallet_id=pallet.absorbed_into_pallet_id,
                absorbed_at=pallet.absorbed_at,
                absorbed_by_user_id=pallet.absorbed_by_user_id,
                created_at=pallet.created_at,
                updated_at=pallet.updated_at,
                created_by_user_id=pallet.created_by_user_id,
                updated_by_user_id=pallet.updated_by_user_id,
                physical_box_count=int(values["physical_box_count"]),
                box_count=int(values["box_count"]),
                status_counts={
                    status.value: int(values[f"{status.value}_count"])
                    for status in BoxStatus
                },
                eligible_box_count=eligible_count,
                completed_box_count=completed_count,
                completion_percent=(
                    round(float(raw_percent), 2) if raw_percent is not None else None
                ),
                progress_state=_progress_state(eligible_count, completed_count),
                latest_activity=values["latest_activity"],
            )
        )
    return summaries, total


def get_visible_pallet_summary(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    include_inactive: bool = False,
    allow_unrepresented_lot: bool = False,
) -> PalletSummary:
    rows, _ = list_pallet_summaries(
        db,
        user=user,
        pallet_id=pallet_id,
        include_inactive=include_inactive,
        allow_unrepresented_lot=allow_unrepresented_lot,
        page=1,
        page_size=1,
    )
    if not rows:
        raise PalletNotFoundError(f"pallet {pallet_id} not found")
    return rows[0]


def list_pallet_options(
    db: Session,
    *,
    user: User,
    search: str | None = None,
    warehouse_id: int | None = None,
    lot_id: int | None = None,
    include_inactive: bool = False,
    page: int = 1,
    limit: int = 25,
) -> tuple[list[PalletOption], int]:
    if include_inactive and user.role != UserRole.admin:
        raise PalletAccessError("inactive pallets require admin role")
    scoped_boxes, represented = _visibility_sources(user)
    stmt = select(Pallet, Lot.name).join(Lot, Lot.id == Pallet.lot_id)
    if not include_inactive:
        stmt = stmt.where(
            Pallet.is_active.is_(True),
            Lot.merged_into_lot_id.is_(None),
        )
    if user.role != UserRole.admin:
        stmt = stmt.where(Pallet.lot_id.in_(select(represented.c.lot_id)))
    if warehouse_id is not None:
        stmt = stmt.where(
            Pallet.id.in_(
                select(scoped_boxes.c.pallet_id).where(
                    scoped_boxes.c.warehouse_id == warehouse_id
                )
            )
        )
    if lot_id is not None:
        stmt = stmt.where(Pallet.lot_id == lot_id)
    exact_normalized: str | None = None
    if search and search.strip():
        cleaned = clean_pallet_number(search)
        exact_normalized = normalize_pallet_number(cleaned)
        stmt = stmt.where(
            or_(
                Pallet.pallet_number.ilike(f"%{cleaned}%"),
                Pallet.normalized_pallet_number == exact_normalized,
                Lot.name.ilike(f"%{cleaned}%"),
                Pallet.id.in_(
                    select(scoped_boxes.c.pallet_id)
                    .join(Warehouse, Warehouse.id == scoped_boxes.c.warehouse_id)
                    .where(Warehouse.name.ilike(f"%{cleaned}%"))
                ),
            )
        )
        stmt = stmt.order_by(
            case((Pallet.normalized_pallet_number == exact_normalized, 0), else_=1),
            func.lower(Pallet.pallet_number),
            Pallet.id,
        )
    else:
        stmt = stmt.order_by(func.lower(Pallet.pallet_number), Pallet.id)
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(stmt.offset((page - 1) * limit).limit(limit)).all()
    warehouse_values = _warehouse_values_for(
        db,
        pallet_ids=[row[0].id for row in rows],
        scoped_boxes=scoped_boxes,
    )
    return (
        [
            PalletOption(
                id=pallet.id,
                pallet_number=pallet.pallet_number,
                normalized_pallet_number=pallet.normalized_pallet_number,
                lot_id=pallet.lot_id,
                lot_name=lot_name,
                warehouse_ids=warehouse_values[pallet.id][0],
                warehouse_names=warehouse_values[pallet.id][1],
                is_active=pallet.is_active,
                exact_normalized_match=(
                    pallet.normalized_pallet_number == exact_normalized
                ),
            )
            for pallet, lot_name in rows
        ],
        total,
    )


def _validate_pallet_create_access(
    db: Session,
    *,
    user: User,
    lot_id: int,
    warehouse_id: int,
) -> tuple[Lot, Warehouse]:
    if user.role not in (UserRole.admin, UserRole.operator):
        raise PalletAccessError("pallet creation requires operator or admin role")
    if not can_access(user, warehouse_id):
        raise PalletAccessError("warehouse access denied")
    lot = db.get(Lot, lot_id)
    if lot is None:
        raise PalletRuleError("lot not found")
    if lot.merged_into_lot_id is not None:
        raise PalletConflictError("cannot create a pallet in a merged lot")
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None or not warehouse.is_active:
        raise PalletConflictError("warehouse not found or archived")
    return lot, warehouse


def resolve_or_create_active_pallet(
    db: Session,
    *,
    user: User,
    lot_id: int,
    warehouse_id: int,
    pallet_number: str,
    pallet_id: int | None = None,
    allow_snapshot_number_mismatch: bool = False,
    receipt_creation_authorized: bool = False,
) -> Pallet:
    """Resolve one active pallet without moving an existing identity.

    The lot is locked before the pallet lookup/insert so receipt paths use the
    same deterministic parent-first lock order as other lot mutations.
    """
    if receipt_creation_authorized:
        if not can_access(user, warehouse_id):
            raise PalletAccessError("warehouse access denied")
        lot = db.get(Lot, lot_id)
        warehouse = db.get(Warehouse, warehouse_id)
        if lot is None or lot.merged_into_lot_id is not None:
            raise PalletConflictError("cannot receive into a missing or merged lot")
        if warehouse is None or not warehouse.is_active:
            raise PalletConflictError("warehouse not found or archived")
    else:
        _validate_pallet_create_access(
            db,
            user=user,
            lot_id=lot_id,
            warehouse_id=warehouse_id,
        )
    cleaned = clean_pallet_number(pallet_number)
    normalized = normalize_pallet_number(cleaned)
    locked_lot = db.scalar(
        select(Lot).where(Lot.id == lot_id).with_for_update(of=Lot)
    )
    if locked_lot is None or locked_lot.merged_into_lot_id is not None:
        raise PalletConflictError("cannot receive into a missing or merged lot")

    if pallet_id is not None:
        pallet = db.scalar(
            select(Pallet)
            .where(Pallet.id == pallet_id)
            .with_for_update(of=Pallet)
        )
        if pallet is None:
            raise PalletConflictError(f"pallet {pallet_id} not found")
        if pallet.lot_id != lot_id:
            raise PalletConflictError("pallet does not belong to the receipt lot")
        if not pallet.is_active:
            raise PalletConflictError("archived pallets cannot receive boxes")
        if (
            not allow_snapshot_number_mismatch
            and pallet.normalized_pallet_number != normalized
        ):
            raise PalletConflictError(
                "pallet_id and pallet_number identify different pallets"
            )
        return pallet

    collision = db.scalar(
        select(Pallet)
        .where(
            Pallet.lot_id == lot_id,
            Pallet.normalized_pallet_number == normalized,
        )
        .with_for_update(of=Pallet)
    )
    if collision is not None:
        if not collision.is_active:
            raise PalletNumberCollisionError(
                f"pallet {collision.pallet_number!r} is archived in this lot"
            )
        return collision

    pallet = Pallet(
        lot_id=lot_id,
        pallet_number=cleaned,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    try:
        with db.begin_nested():
            db.add(pallet)
            db.flush()
            db.add(
                PalletEvent(
                    pallet_id=pallet.id,
                    event_type=PalletEventType.created,
                    new_pallet_number=pallet.pallet_number,
                    actor_user_id=user.id,
                    event_metadata={
                        "operation": "receipt_resolve_or_create",
                        "authorization_warehouse_id": warehouse_id,
                    },
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise PalletNumberCollisionError(
            f"pallet {cleaned!r} was created concurrently in this lot; retry"
        ) from exc
    return pallet


def create_pallet(
    db: Session,
    *,
    user: User,
    lot_id: int,
    warehouse_id: int,
    pallet_number: str,
) -> Pallet:
    _validate_pallet_create_access(
        db,
        user=user,
        lot_id=lot_id,
        warehouse_id=warehouse_id,
    )
    cleaned = clean_pallet_number(pallet_number)
    normalized = normalize_pallet_number(cleaned)
    collision = db.scalar(
        select(Pallet).where(
            Pallet.lot_id == lot_id,
            Pallet.normalized_pallet_number == normalized,
        )
    )
    if collision is not None:
        raise PalletNumberCollisionError(
            f"pallet {collision.pallet_number!r} already exists in this lot"
        )
    pallet = Pallet(
        lot_id=lot_id,
        pallet_number=cleaned,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
    )
    db.add(pallet)
    try:
        db.flush()
        db.add(
            PalletEvent(
                pallet_id=pallet.id,
                event_type=PalletEventType.created,
                new_pallet_number=pallet.pallet_number,
                actor_user_id=user.id,
                event_metadata={
                    "operation": "create",
                    "authorization_warehouse_id": warehouse_id,
                },
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PalletNumberCollisionError(
            f"pallet {cleaned!r} already exists in this lot"
        ) from exc
    db.refresh(pallet)
    return pallet


def rename_pallet(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    new_pallet_number: str,
    reason: str,
    expected_version: int,
) -> Pallet:
    if user.role != UserRole.admin:
        raise PalletAccessError("pallet rename requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise PalletRuleError("a reason is required to rename a pallet")
    pallet = _visible_pallet(
        db, user=user, pallet_id=pallet_id, include_inactive=True, lock=True
    )
    if not pallet.is_active:
        raise PalletConflictError("archived pallets cannot be renamed")
    _validate_mutable_parents(db, pallet)
    if pallet.version != expected_version:
        raise PalletVersionConflictError(
            f"pallet version conflict: expected {expected_version}, "
            f"current {pallet.version}"
        )
    cleaned = clean_pallet_number(new_pallet_number)
    normalized = normalize_pallet_number(cleaned)
    collision = db.scalar(
        select(Pallet).where(
            Pallet.lot_id == pallet.lot_id,
            Pallet.normalized_pallet_number == normalized,
            Pallet.id != pallet.id,
        )
    )
    if collision is not None:
        raise PalletNumberCollisionError(
            f"pallet {collision.pallet_number!r} already exists in this lot"
        )
    old_number = pallet.pallet_number
    if old_number == cleaned:
        return pallet
    pallet.pallet_number = cleaned
    pallet.updated_by_user_id = user.id
    db.add(
        PalletEvent(
            pallet_id=pallet.id,
            event_type=PalletEventType.renumbered,
            old_pallet_number=old_number,
            new_pallet_number=cleaned,
            actor_user_id=user.id,
            reason=cleaned_reason,
            event_metadata={"expected_version": expected_version},
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PalletNumberCollisionError(
            f"pallet {cleaned!r} already exists in this lot"
        ) from exc
    db.refresh(pallet)
    return pallet


def archive_pallet(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    reason: str,
    expected_version: int,
) -> Pallet:
    if user.role != UserRole.admin:
        raise PalletAccessError("pallet archive requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise PalletRuleError("a reason is required to archive a pallet")
    pallet = _visible_pallet(
        db, user=user, pallet_id=pallet_id, include_inactive=True, lock=True
    )
    if not pallet.is_active:
        raise PalletConflictError("pallet is already archived")
    _validate_mutable_parents(db, pallet)
    if pallet.version != expected_version:
        raise PalletVersionConflictError(
            f"pallet version conflict: expected {expected_version}, "
            f"current {pallet.version}"
        )
    box_count = int(
        db.scalar(select(func.count(Box.id)).where(Box.pallet_id == pallet.id)) or 0
    )
    if box_count:
        raise PalletArchiveConflictError(
            "pallet cannot be archived while boxes are assigned",
            box_count=box_count,
        )
    now = datetime.now(UTC)
    pallet.is_active = False
    pallet.archived_at = now
    pallet.archived_by_user_id = user.id
    pallet.archive_reason = cleaned_reason
    pallet.updated_by_user_id = user.id
    db.add(
        PalletEvent(
            pallet_id=pallet.id,
            event_type=PalletEventType.archived,
            old_pallet_number=pallet.pallet_number,
            actor_user_id=user.id,
            reason=cleaned_reason,
            event_metadata={"expected_version": expected_version},
        )
    )
    db.commit()
    db.refresh(pallet)
    return pallet


def restore_pallet(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    reason: str,
    expected_version: int,
) -> Pallet:
    if user.role != UserRole.admin:
        raise PalletAccessError("pallet restore requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise PalletRuleError("a reason is required to restore a pallet")
    pallet = _visible_pallet(
        db, user=user, pallet_id=pallet_id, include_inactive=True, lock=True
    )
    if pallet.is_active:
        raise PalletConflictError("pallet is already active")
    if pallet.absorbed_into_pallet_id is not None:
        raise PalletConflictError(
            "a pallet absorbed by a lot merge cannot be restored"
        )
    _validate_mutable_parents(db, pallet)
    if pallet.version != expected_version:
        raise PalletVersionConflictError(
            f"pallet version conflict: expected {expected_version}, "
            f"current {pallet.version}"
        )
    box_count = int(
        db.scalar(select(func.count(Box.id)).where(Box.pallet_id == pallet.id)) or 0
    )
    if box_count:
        raise PalletArchiveConflictError(
            "pallet cannot be restored while boxes are assigned",
            box_count=box_count,
        )
    pallet.is_active = True
    pallet.archived_at = None
    pallet.archived_by_user_id = None
    pallet.archive_reason = None
    pallet.updated_by_user_id = user.id
    db.add(
        PalletEvent(
            pallet_id=pallet.id,
            event_type=PalletEventType.restored,
            new_pallet_number=pallet.pallet_number,
            actor_user_id=user.id,
            reason=cleaned_reason,
            event_metadata={"expected_version": expected_version},
        )
    )
    db.commit()
    db.refresh(pallet)
    return pallet


def _add_pallet_box_event(
    db: Session,
    *,
    pallet: Pallet,
    event_type: PalletEventType,
    box_ids: list[int],
    user: User,
    reason: str,
    operation: str,
) -> None:
    if not box_ids:
        return
    db.add(
        PalletEvent(
            pallet_id=pallet.id,
            event_type=event_type,
            actor_user_id=user.id,
            reason=reason,
            event_metadata={
                "operation": operation,
                "box_ids": sorted(box_ids),
                "box_count": len(box_ids),
            },
        )
    )


def mutate_pallet_boxes(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    box_ids: list[int],
    reason: str,
    detach: bool = False,
) -> PalletBoxMutationResult:
    """Assign/reassign or detach boxes with deterministic pallet/box locks."""
    if user.role not in (UserRole.admin, UserRole.operator):
        raise PalletAccessError("pallet assignment requires operator or admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise PalletRuleError("a reason is required to change pallet assignments")
    unique_ids = sorted(set(box_ids))
    if not unique_ids or len(unique_ids) > 500:
        raise PalletRuleError("box_ids must contain between 1 and 500 unique IDs")

    visible_target = _visible_pallet(
        db,
        user=user,
        pallet_id=pallet_id,
        include_inactive=user.role == UserRole.admin,
        lock=True,
    )
    source_pallet_ids = set(
        db.scalars(
            select(Box.pallet_id).where(
                Box.id.in_(unique_ids),
                Box.pallet_id.is_not(None),
            )
        ).all()
    )
    pallet_ids = sorted({pallet_id, *source_pallet_ids})
    locked_pallets = {
        pallet.id: pallet
        for pallet in db.scalars(
            select(Pallet)
            .where(Pallet.id.in_(pallet_ids))
            .order_by(Pallet.id)
            .with_for_update(of=Pallet)
        ).all()
    }
    target = locked_pallets.get(pallet_id)
    assert target is not None and target.id == visible_target.id
    if not target.is_active and not detach:
        raise PalletConflictError("archived pallets cannot change assignments")
    _validate_mutable_parents(db, target)

    boxes = {
        box.id: box
        for box in db.scalars(
            select(Box)
            .where(Box.id.in_(unique_ids))
            .order_by(Box.id)
            .with_for_update(of=Box)
        ).all()
    }
    updated: list[int] = []
    skipped: list[dict[str, object]] = []
    removed_by_pallet: dict[int, list[int]] = {}
    affected_pallet_warehouse_ids: dict[int, set[int]] = {}
    assigned: list[int] = []
    for box_id in unique_ids:
        box = boxes.get(box_id)
        if box is None:
            skipped.append({"box_id": box_id, "reason": "box not found"})
            continue
        if not can_access(user, box.current_warehouse_id):
            skipped.append({"box_id": box_id, "reason": "warehouse access denied"})
            continue
        if detach:
            if box.pallet_id != target.id:
                skipped.append(
                    {"box_id": box.id, "reason": "box is not assigned to this pallet"}
                )
                continue
            source_id = set_box_pallet_assignment(
                db,
                user=user,
                box=box,
                target_pallet=None,
                reason=cleaned_reason,
                metadata={"operation": "pallet_detach"},
                record_pallet_event=False,
            )
            assert source_id is not None
            removed_by_pallet.setdefault(source_id, []).append(box.id)
            affected_pallet_warehouse_ids.setdefault(source_id, set()).add(
                box.current_warehouse_id
            )
        else:
            if box.archived_at is not None:
                skipped.append({"box_id": box.id, "reason": "box is archived"})
                continue
            if box.lot_id != target.lot_id:
                skipped.append(
                    {
                        "box_id": box.id,
                        "reason": "box must match pallet lot",
                    }
                )
                continue
            source_id = set_box_pallet_assignment(
                db,
                user=user,
                box=box,
                target_pallet=target,
                reason=cleaned_reason,
                metadata={"operation": "pallet_assignment"},
                record_pallet_event=False,
            )
            if source_id == target.id:
                skipped.append({"box_id": box.id, "reason": "already assigned"})
                continue
            if source_id is not None:
                removed_by_pallet.setdefault(source_id, []).append(box.id)
                affected_pallet_warehouse_ids.setdefault(source_id, set()).add(
                    box.current_warehouse_id
                )
            affected_pallet_warehouse_ids.setdefault(target.id, set()).add(
                box.current_warehouse_id
            )
            assigned.append(box.id)
        updated.append(box.id)

    for source_id, removed_ids in removed_by_pallet.items():
        if detach and source_id == target.id:
            continue
        source = locked_pallets.get(source_id)
        if source is not None:
            _add_pallet_box_event(
                db,
                pallet=source,
                event_type=PalletEventType.boxes_unassigned,
                box_ids=removed_ids,
                user=user,
                reason=cleaned_reason,
                operation="bulk_detach" if detach else "bulk_reassign_source",
            )
    _add_pallet_box_event(
        db,
        pallet=target,
        event_type=(
            PalletEventType.boxes_unassigned
            if detach
            else PalletEventType.boxes_assigned
        ),
        box_ids=updated if detach else assigned,
        user=user,
        reason=cleaned_reason,
        operation="bulk_detach" if detach else "bulk_assign",
    )
    if updated:
        target.updated_by_user_id = user.id
        db.commit()
    return PalletBoxMutationResult(
        pallet_id=target.id,
        updated_box_ids=updated,
        skipped=skipped,
        cancelled_request_ids=[],
        affected_pallet_warehouse_ids=affected_pallet_warehouse_ids,
    )


def pallet_integrity_report(db: Session) -> dict[str, object]:
    """Return pallet conflicts plus explicitly informational assignment counts."""
    active_boxes = Box.archived_at.is_(None)
    rows = {
        "orphaned_pallet_ids": db.scalars(
            select(Box.id)
            .outerjoin(Pallet, Pallet.id == Box.pallet_id)
            .where(active_boxes, Box.pallet_id.is_not(None), Pallet.id.is_(None))
            .order_by(Box.id)
        ).all(),
        "cross_lot": db.scalars(
            select(Box.id)
            .join(Pallet, Pallet.id == Box.pallet_id)
            .where(active_boxes, Box.lot_id != Pallet.lot_id)
            .order_by(Box.id)
        ).all(),
        "inactive_pallet_assignments": db.scalars(
            select(Box.id)
            .join(Pallet, Pallet.id == Box.pallet_id)
            .where(active_boxes, Pallet.is_active.is_(False))
            .order_by(Box.id)
        ).all(),
        "unassigned_active_boxes": db.scalars(
            select(Box.id)
            .where(
                active_boxes,
                Box.status.in_(ACTIVE_STATUSES),
                Box.pallet_id.is_(None),
            )
            .order_by(Box.id)
        ).all(),
    }
    groups = {
        name: {"count": len(ids), "box_ids": [int(box_id) for box_id in ids]}
        for name, ids in rows.items()
    }
    conflict_count = sum(
        len(rows[name])
        for name in (
            "orphaned_pallet_ids",
            "cross_lot",
            "inactive_pallet_assignments",
        )
    )
    informational = groups["unassigned_active_boxes"]
    return {
        "safe": conflict_count == 0,
        "conflict_count": conflict_count,
        "informational_count": len(rows["unassigned_active_boxes"]),
        **groups,
        "informational": {
            "unassigned_active_boxes": informational,
        },
    }


def list_pallet_events(
    db: Session,
    *,
    user: User,
    pallet_id: int,
    include_inactive: bool = False,
) -> list[PalletEventView]:
    _visible_pallet(
        db,
        user=user,
        pallet_id=pallet_id,
        include_inactive=include_inactive,
    )
    events = list(
        db.scalars(
            select(PalletEvent)
            .where(PalletEvent.pallet_id == pallet_id)
            .order_by(PalletEvent.occurred_at.desc(), PalletEvent.id.desc())
        ).all()
    )
    allowed = allowed_warehouse_ids(user)
    return [
        PalletEventView(
            id=event.id,
            pallet_id=event.pallet_id,
            event_type=event.event_type,
            old_pallet_number=event.old_pallet_number,
            new_pallet_number=event.new_pallet_number,
            from_warehouse_id=(
                event.from_warehouse_id
                if allowed is None or event.from_warehouse_id in allowed
                else None
            ),
            to_warehouse_id=(
                event.to_warehouse_id
                if allowed is None or event.to_warehouse_id in allowed
                else None
            ),
            actor_user_id=event.actor_user_id,
            reason=event.reason,
            occurred_at=event.occurred_at,
            event_metadata=(
                dict(event.event_metadata or {})
                if allowed is None
                else {
                    key: value
                    for key, value in (event.event_metadata or {}).items()
                    if key in {"operation", "expected_version", "admin_override"}
                }
            ),
        )
        for event in events
    ]


__all__ = [
    "PalletAccessError",
    "PalletArchiveConflictError",
    "PalletConflictError",
    "PalletNotFoundError",
    "PalletNumberCollisionError",
    "PalletOption",
    "PalletBoxMutationResult",
    "PalletEventView",
    "PalletProgressState",
    "PalletRuleError",
    "PalletSortField",
    "PalletSummary",
    "PalletVersionConflictError",
    "archive_pallet",
    "create_pallet",
    "get_visible_pallet_summary",
    "list_pallet_events",
    "list_pallet_options",
    "list_pallet_summaries",
    "mutate_pallet_boxes",
    "pallet_integrity_report",
    "rename_pallet",
    "resolve_or_create_active_pallet",
    "restore_pallet",
]
