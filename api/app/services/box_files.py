from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.box_files import (
    BoxFile,
    BoxFileEvent,
    BoxFileEventType,
    normalize_box_file_reference,
)
from app.models.boxes import Box, BoxStatus
from app.models.lots import Lot
from app.models.pallets import Pallet
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
    BoxRequestItemFileSnapshot,
    BoxRequestItemFileSnapshotKind,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.acl import allowed_warehouse_ids, can_access
from app.services.boxes import _cancel_active_returns


class BoxFileRuleError(ValueError):
    pass


class BoxFileAccessError(BoxFileRuleError):
    pass


class BoxFileNotFoundError(BoxFileRuleError):
    pass


class BoxFileConflictError(BoxFileRuleError):
    code = "file_conflict"


class BoxFileVersionConflictError(BoxFileConflictError):
    code = "version_conflict"


class BoxFileReferenceConflictError(BoxFileConflictError):
    code = "reference_conflict"


@dataclass(frozen=True)
class InheritedBoxChange:
    box_id: int
    event_type: BoxFileEventType
    from_warehouse_id: int
    to_warehouse_id: int
    from_status: BoxStatus
    to_status: BoxStatus


@dataclass(frozen=True)
class IntakeFile:
    reference: str
    description: str | None = None
    barcode: str | None = None
    legacy: bool = False


@dataclass
class IntakeFileOutcome:
    created: list[BoxFile]
    updated: list[BoxFile]
    moved: list[BoxFile]
    preserved: list[BoxFile]


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def split_legacy_contents(contents: str | None) -> list[str]:
    if not contents:
        return []
    return [part.strip() for part in contents.split("|") if part.strip()]


def legacy_intake_reference(box: Box, ordinal: int) -> str:
    clean_number = " ".join(box.box_number.split())
    # References are unique within a lot, so the canonical box identity is
    # sufficient and remains stable before/after a staged box receives an ID.
    return f"LEGACY-BOX-{clean_number}-FILE-{ordinal}"


def intake_file_values(
    box: Box,
    *,
    files: list[object] | None,
    contents: str | None,
) -> list[IntakeFile]:
    """Canonicalize structured inputs, falling back to legacy contents.

    ``files=[]`` is explicit and therefore never falls back to ``contents``.
    Exact duplicate rows are harmless; duplicate references with conflicting
    metadata are rejected before any rows are written.
    """
    raw: list[IntakeFile]
    if files is not None:
        raw = [
            IntakeFile(
                reference=str(item.reference),  # type: ignore[attr-defined]
                description=_clean_optional(getattr(item, "description", None)),
                barcode=_clean_optional(getattr(item, "barcode", None)),
            )
            for item in files
        ]
    else:
        raw = [
            IntakeFile(
                reference=legacy_intake_reference(box, position),
                description=description,
                legacy=True,
            )
            for position, description in enumerate(
                split_legacy_contents(contents), start=1
            )
        ]
    unique: dict[str, IntakeFile] = {}
    for value in raw:
        normalized = normalize_box_file_reference(value.reference)
        canonical = IntakeFile(
            reference=" ".join(value.reference.split()),
            description=_clean_optional(value.description),
            barcode=_clean_optional(value.barcode),
            legacy=value.legacy,
        )
        existing = unique.get(normalized)
        if existing is not None and existing != canonical:
            raise BoxFileReferenceConflictError(
                f"file reference {canonical.reference!r} is repeated with "
                "conflicting details"
            )
        unique.setdefault(normalized, canonical)
    return list(unique.values())


def _view_stmt():
    return (
        select(
            BoxFile.id,
            BoxFile.reference,
            BoxFile.description,
            BoxFile.barcode,
            BoxFile.position,
            BoxFile.lot_id,
            Lot.name.label("lot"),
            Box.pallet_id,
            Pallet.pallet_number.label("pallet"),
            BoxFile.box_id,
            Box.box_number.label("box"),
            Box.current_warehouse_id.label("warehouse_id"),
            Warehouse.name.label("warehouse"),
            Box.status,
            BoxFile.archived_at,
            BoxFile.archived_by_user_id,
            BoxFile.archive_reason,
            BoxFile.version,
            BoxFile.created_at,
            BoxFile.updated_at,
            BoxFile.created_by_user_id,
            BoxFile.updated_by_user_id,
            Box.archived_at.label("box_archived_at"),
            Lot.merged_into_lot_id,
        )
        .join(Box, Box.id == BoxFile.box_id)
        .join(Lot, Lot.id == BoxFile.lot_id)
        .join(Warehouse, Warehouse.id == Box.current_warehouse_id)
        .outerjoin(Pallet, Pallet.id == Box.pallet_id)
    )


def _view(row) -> dict[str, object]:
    values = dict(row._mapping)
    values["is_active"] = (
        values.pop("box_archived_at") is None
        and values.pop("merged_into_lot_id") is None
        and values["archived_at"] is None
    )
    return values


def _json_snapshot(values: dict[str, object]) -> dict[str, object]:
    return {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else value.value
            if isinstance(value, enum.Enum)
            else value
        )
        for key, value in values.items()
    }


def list_box_files(
    db: Session,
    *,
    user: User,
    search: str | None = None,
    warehouse_id: int | None = None,
    lot_id: int | None = None,
    pallet_id: int | None = None,
    box_id: int | None = None,
    status: BoxStatus | None = None,
    activity: Literal["active", "archived", "all"] | None = None,
    include_inactive: bool = False,
    sort_by: Literal[
        "reference",
        "lot",
        "pallet",
        "box",
        "warehouse",
        "status",
        "position",
        "created_at",
        "updated_at",
    ] | None = None,
    sort_dir: Literal["asc", "desc"] = "asc",
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, object]], int]:
    if include_inactive and user.role != UserRole.admin:
        raise BoxFileAccessError("inactive files require admin role")
    stmt = _view_stmt()
    allowed = allowed_warehouse_ids(user)
    if allowed is not None:
        stmt = stmt.where(Box.current_warehouse_id.in_(allowed))
    if not include_inactive:
        stmt = stmt.where(
            Box.archived_at.is_(None),
            Lot.merged_into_lot_id.is_(None),
        )
    if activity == "archived":
        stmt = stmt.where(BoxFile.archived_at.is_not(None))
    elif activity != "all":
        stmt = stmt.where(BoxFile.archived_at.is_(None))
    if warehouse_id is not None:
        stmt = stmt.where(Box.current_warehouse_id == warehouse_id)
    if lot_id is not None:
        stmt = stmt.where(BoxFile.lot_id == lot_id)
    if pallet_id is not None:
        stmt = stmt.where(Box.pallet_id == pallet_id)
    if box_id is not None:
        stmt = stmt.where(BoxFile.box_id == box_id)
    if status is not None:
        stmt = stmt.where(Box.status == status)
    if search and search.strip():
        needle = f"%{' '.join(search.split())}%"
        stmt = stmt.where(
            or_(
                BoxFile.reference.ilike(needle),
                BoxFile.description.ilike(needle),
                BoxFile.barcode.ilike(needle),
                Box.box_number.ilike(needle),
                Lot.name.ilike(needle),
                Pallet.pallet_number.ilike(needle),
                Warehouse.name.ilike(needle),
            )
        )
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(db.scalar(count_stmt) or 0)
    if sort_by is None:
        order_by = (Lot.name, Box.box_number, BoxFile.position, BoxFile.id)
    else:
        sort_column = {
            "reference": func.lower(BoxFile.reference),
            "lot": func.lower(Lot.name),
            "pallet": func.lower(Pallet.pallet_number),
            "box": func.lower(Box.box_number),
            "warehouse": func.lower(Warehouse.name),
            "status": Box.status,
            "position": BoxFile.position,
            "created_at": BoxFile.created_at,
            "updated_at": BoxFile.updated_at,
        }[sort_by]
        ordered = sort_column.asc() if sort_dir == "asc" else sort_column.desc()
        order_by = (ordered.nulls_last(), BoxFile.id.asc())
    rows = db.execute(
        stmt.order_by(*order_by)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [_view(row) for row in rows], total


def file_integrity_report(db: Session) -> dict[str, object]:
    """Return read-only findings for the complete tracked File graph."""

    def ids(statement) -> list[int]:
        return [int(value) for value in db.scalars(statement).all()]

    cross_lot = ids(
        select(BoxFile.id)
        .join(Box, Box.id == BoxFile.box_id)
        .where(BoxFile.lot_id != Box.lot_id)
        .order_by(BoxFile.id)
    )
    duplicate_refs = [
        {
            "lot_id": int(lot_id),
            "normalized_reference": str(reference),
            "file_ids": ids(
                select(BoxFile.id)
                .where(
                    BoxFile.lot_id == lot_id,
                    BoxFile.normalized_reference == reference,
                )
                .order_by(BoxFile.id)
            ),
        }
        for lot_id, reference in db.execute(
            select(BoxFile.lot_id, BoxFile.normalized_reference)
            .group_by(BoxFile.lot_id, BoxFile.normalized_reference)
            .having(func.count(BoxFile.id) > 1)
            .order_by(BoxFile.lot_id, BoxFile.normalized_reference)
        ).all()
    ]
    invalid_positions = set(
        ids(select(BoxFile.id).where(BoxFile.position <= 0).order_by(BoxFile.id))
    )
    for box_id, position in db.execute(
        select(BoxFile.box_id, BoxFile.position)
        .group_by(BoxFile.box_id, BoxFile.position)
        .having(func.count(BoxFile.id) > 1)
    ).all():
        invalid_positions.update(
            ids(
                select(BoxFile.id)
                .where(
                    BoxFile.box_id == box_id,
                    BoxFile.position == position,
                )
                .order_by(BoxFile.id)
            )
        )
    archived_in_workflows = ids(
        select(BoxFile.id)
        .where(
            BoxFile.archived_at.is_not(None),
            BoxFile.box_id.in_(
                select(BoxRequestItem.box_id)
                .join(BoxRequest, BoxRequest.id == BoxRequestItem.request_id)
                .where(
                    BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
                    BoxRequestItem.box_id.is_not(None),
                )
            ),
        )
        .order_by(BoxFile.id)
    )
    detached_snapshots = ids(
        select(BoxRequestItemFileSnapshot.id)
        .where(
            BoxRequestItemFileSnapshot.snapshot_kind
            == BoxRequestItemFileSnapshotKind.tracked_file,
            BoxRequestItemFileSnapshot.file_id.is_(None),
        )
        .order_by(BoxRequestItemFileSnapshot.id)
    )
    groups: dict[str, dict[str, object]] = {
        "cross_lot_placements": {"count": len(cross_lot), "file_ids": cross_lot},
        "duplicate_normalized_references": {
            "count": len(duplicate_refs),
            "groups": duplicate_refs,
        },
        "invalid_positions": {
            "count": len(invalid_positions),
            "file_ids": sorted(invalid_positions),
        },
        "archived_files_in_active_workflows": {
            "count": len(archived_in_workflows),
            "file_ids": archived_in_workflows,
        },
        "detached_tracked_snapshots": {
            "count": len(detached_snapshots),
            "snapshot_ids": detached_snapshots,
        },
    }
    conflict_count = sum(int(group["count"]) for group in groups.values())
    return {"safe": conflict_count == 0, "conflict_count": conflict_count, **groups}


def get_box_file(
    db: Session,
    *,
    user: User,
    file_id: int,
    include_inactive: bool = False,
    include_archived: bool = False,
) -> dict[str, object]:
    if include_inactive and user.role != UserRole.admin:
        raise BoxFileAccessError("inactive files require admin role")
    stmt = _view_stmt().where(BoxFile.id == file_id)
    allowed = allowed_warehouse_ids(user)
    if allowed is not None:
        stmt = stmt.where(Box.current_warehouse_id.in_(allowed))
    if not include_inactive:
        stmt = stmt.where(
            Box.archived_at.is_(None),
            Lot.merged_into_lot_id.is_(None),
        )
    if not include_archived:
        stmt = stmt.where(BoxFile.archived_at.is_(None))
    row = db.execute(stmt).first()
    if row is None:
        raise BoxFileNotFoundError(f"file {file_id} not found")
    return _view(row)


def list_box_file_events(
    db: Session,
    *,
    user: User,
    file_id: int,
) -> list[dict[str, object]]:
    # Event visibility follows the file's current box, including archived files.
    get_box_file(
        db,
        user=user,
        file_id=file_id,
        include_inactive=user.role == UserRole.admin,
        include_archived=True,
    )
    events = list(
        db.scalars(
            select(BoxFileEvent)
            .where(BoxFileEvent.file_id == file_id)
            .order_by(BoxFileEvent.occurred_at.desc(), BoxFileEvent.id.desc())
        ).all()
    )
    def visible_snapshot(snapshot: dict[str, object] | None) -> dict[str, object] | None:
        if snapshot is None or user.role == UserRole.admin:
            return snapshot
        warehouse_id = snapshot.get("warehouse_id")
        if not isinstance(warehouse_id, int) or can_access(user, warehouse_id):
            return snapshot
        redacted = dict(snapshot)
        for key in ("warehouse_id", "warehouse", "pallet_id", "pallet", "box_id", "box"):
            redacted[key] = None
        return redacted

    def visible_metadata(value: object, key: str = "") -> object:
        if user.role == UserRole.admin:
            return value
        if isinstance(value, dict):
            return {
                child_key: visible_metadata(child_value, child_key)
                for child_key, child_value in value.items()
            }
        if key.endswith("warehouse_id") and isinstance(value, int):
            return value if can_access(user, value) else None
        if key.endswith("warehouse_ids") and isinstance(value, list):
            return [
                warehouse_id
                for warehouse_id in value
                if isinstance(warehouse_id, int) and can_access(user, warehouse_id)
            ]
        if key.endswith(("pallet_id", "box_id")) and isinstance(value, int):
            return None
        if key.endswith(("pallet_ids", "box_ids")) and isinstance(value, list):
            return []
        return value

    return [
        {
            "id": event.id,
            "file_id": event.file_id,
            "event_type": event.event_type,
            "before_snapshot": visible_snapshot(event.before_snapshot),
            "after_snapshot": visible_snapshot(event.after_snapshot),
            "actor_user_id": event.actor_user_id,
            "reason": event.reason,
            "occurred_at": event.occurred_at,
            "event_metadata": visible_metadata(event.event_metadata),
        }
        for event in events
    ]


def _lock_box(db: Session, box_id: int) -> tuple[Box, Lot]:
    row = db.execute(
        select(Box, Lot)
        .join(Lot, Lot.id == Box.lot_id)
        .where(Box.id == box_id)
        .with_for_update(of=(Box, Lot))
    ).first()
    if row is None:
        raise BoxFileNotFoundError(f"box {box_id} not found")
    box, lot = row
    if lot.merged_into_lot_id is not None:
        raise BoxFileConflictError("archived or merged lots cannot contain mutable files")
    if box.archived_at is not None:
        raise BoxFileConflictError("archived boxes cannot contain mutable files")
    return box, lot


def _lock_file(db: Session, file_id: int) -> BoxFile:
    file = db.scalar(
        select(BoxFile).where(BoxFile.id == file_id).with_for_update(of=BoxFile)
    )
    if file is None:
        raise BoxFileNotFoundError(f"file {file_id} not found")
    return file


def _preliminary_box_id(db: Session, file_id: int) -> int:
    box_id = db.scalar(select(BoxFile.box_id).where(BoxFile.id == file_id))
    if box_id is None:
        raise BoxFileNotFoundError(f"file {file_id} not found")
    return box_id


def _files_for_boxes(db: Session, box_ids: list[int]) -> dict[int, list[BoxFile]]:
    result = {box_id: [] for box_id in box_ids}
    if not box_ids:
        return result
    files = db.scalars(
        select(BoxFile)
        .where(BoxFile.box_id.in_(box_ids))
        .order_by(BoxFile.box_id, BoxFile.position, BoxFile.id)
        .with_for_update(of=BoxFile)
    ).all()
    for file in files:
        result[file.box_id].append(file)
    return result


def _active_first(files: list[BoxFile]) -> list[BoxFile]:
    return sorted(
        files,
        key=lambda item: (
            item.archived_at is not None,
            item.position,
            item.id or 0,
        ),
    )


def _insert_at(files: list[BoxFile], file: BoxFile, position: int | None) -> list[BoxFile]:
    active_count = sum(item.archived_at is None for item in files)
    target = active_count + 1 if position is None else min(position, active_count + 1)
    active = [item for item in files if item.archived_at is None and item is not file]
    archived = [item for item in files if item.archived_at is not None and item is not file]
    active.insert(target - 1, file)
    return active + archived


def _persist_orders(db: Session, orders: dict[int, list[BoxFile]]) -> None:
    # Positive temporary positions satisfy the check constraint and avoid
    # transient unique collisions on databases with immediate constraints.
    all_files = [file for files in orders.values() for file in files]
    changed = [
        file
        for box_id, files in orders.items()
        for position, file in enumerate(files, start=1)
        if file.box_id != box_id or file.position != position
    ]
    if not changed:
        return
    ceiling = max((item.position for item in all_files), default=0) + len(all_files) + 1000
    for offset, file in enumerate(changed, start=1):
        file.position = ceiling + offset
    db.flush()
    for box_id, files in orders.items():
        for position, file in enumerate(files, start=1):
            file.box_id = box_id
            file.position = position
    db.flush()


def _snapshot(
    db: Session,
    file: BoxFile,
    *,
    box: Box | None = None,
    lot: Lot | None = None,
) -> dict[str, object]:
    box = box or db.get(Box, file.box_id)
    lot = lot or db.get(Lot, file.lot_id)
    if box is None or lot is None:
        raise BoxFileConflictError("file hierarchy is incomplete")
    pallet = db.get(Pallet, box.pallet_id) if box.pallet_id is not None else None
    warehouse = db.get(Warehouse, box.current_warehouse_id)
    return {
        "id": file.id,
        "reference": file.reference,
        "description": file.description,
        "barcode": file.barcode,
        "position": file.position,
        "lot_id": file.lot_id,
        "lot": lot.name,
        "pallet_id": box.pallet_id,
        "pallet": pallet.pallet_number if pallet is not None else None,
        "box_id": file.box_id,
        "box": box.box_number,
        "warehouse_id": box.current_warehouse_id,
        "warehouse": warehouse.name if warehouse is not None else None,
        "status": box.status.value,
        "is_active": (
            file.archived_at is None
            and box.archived_at is None
            and lot.merged_into_lot_id is None
        ),
        "archived_at": file.archived_at.isoformat() if file.archived_at else None,
        "archived_by_user_id": file.archived_by_user_id,
        "archive_reason": file.archive_reason,
        "version": file.version,
        "created_at": file.created_at.isoformat() if file.created_at else None,
        "updated_at": file.updated_at.isoformat() if file.updated_at else None,
        "created_by_user_id": file.created_by_user_id,
        "updated_by_user_id": file.updated_by_user_id,
    }


def _add_event(
    db: Session,
    *,
    file: BoxFile,
    event_type: BoxFileEventType,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    user: User,
    reason: str | None = None,
    metadata: dict[str, object] | None = None,
    now: datetime | None = None,
) -> None:
    db.add(
        BoxFileEvent(
            file_id=file.id,
            event_type=event_type,
            before_snapshot=before,
            after_snapshot=after,
            actor_user_id=user.id,
            reason=reason,
            event_metadata=metadata or {},
            occurred_at=now or datetime.now(UTC),
        )
    )


def _check_version(file: BoxFile, expected_version: int) -> None:
    if file.version != expected_version:
        raise BoxFileVersionConflictError(
            f"file version conflict: expected {expected_version}, current {file.version}"
        )


def _check_access(user: User, box: Box) -> None:
    if not can_access(user, box.current_warehouse_id):
        raise BoxFileAccessError("warehouse access denied")


def _active_return_requests(db: Session, box_id: int) -> list[BoxRequest]:
    return list(
        db.scalars(
            select(BoxRequest)
            .where(
                BoxRequest.id.in_(
                    select(BoxRequestItem.request_id).where(
                        BoxRequestItem.box_id == box_id
                    )
                ),
                BoxRequest.direction == BoxRequestDirection.return_,
                BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            )
            .with_for_update(of=BoxRequest)
        ).all()
    )


def _guard_reservation(
    db: Session,
    *,
    box: Box,
    user: User,
    force: bool,
    reason: str | None,
    now: datetime,
) -> list[int]:
    requests = _active_return_requests(db, box.id)
    if not requests:
        return []
    if not force:
        joined = ", ".join(f"#{request.id}" for request in requests)
        raise BoxFileConflictError(
            f"box is reserved by active return request(s) {joined}"
        )
    if user.role != UserRole.admin:
        raise BoxFileAccessError("force override requires admin role")
    cleaned_reason = (reason or "").strip()
    if not cleaned_reason:
        raise BoxFileRuleError("a reason is required for an admin override")
    return sorted(
        _cancel_active_returns(
            db,
            box_id=box.id,
            user=user,
            reason=cleaned_reason,
            now=now,
        )
    )


def _force_is_valid(user: User, force: bool, reason: str | None) -> None:
    if not force:
        return
    if user.role != UserRole.admin:
        raise BoxFileAccessError("force override requires admin role")
    if not (reason or "").strip():
        raise BoxFileRuleError("a reason is required for an admin override")


def reconcile_intake_box_files(
    db: Session,
    *,
    user: User,
    box: Box,
    files: list[object] | None,
    contents: str | None,
    allow_moves: bool = False,
    reason: str | None = None,
) -> IntakeFileOutcome:
    """Apply one intake row set without committing the surrounding transaction."""
    values = intake_file_values(box, files=files, contents=contents)
    normalized_values = {
        normalize_box_file_reference(value.reference): value for value in values
    }
    target_files = list(
        db.scalars(
            select(BoxFile)
            .where(BoxFile.box_id == box.id)
            .order_by(BoxFile.position, BoxFile.id)
            .with_for_update(of=BoxFile)
        ).all()
    )
    matches = (
        list(
            db.scalars(
                select(BoxFile)
                .where(
                    BoxFile.lot_id == box.lot_id,
                    BoxFile.normalized_reference.in_(sorted(normalized_values)),
                )
                .order_by(BoxFile.id)
                .with_for_update(of=BoxFile)
            ).all()
        )
        if normalized_values
        else []
    )
    matches_by_reference = {item.normalized_reference: item for item in matches}
    source_box_ids = sorted(
        {item.box_id for item in matches if item.box_id != box.id}
    )
    source_files = _files_for_boxes(db, source_box_ids)
    boxes_by_id = {
        item.id: item
        for item in db.scalars(
            select(Box)
            .where(Box.id.in_(source_box_ids))
            .order_by(Box.id)
            .with_for_update(of=Box)
        ).all()
    }
    if len(boxes_by_id) != len(source_box_ids):
        raise BoxFileConflictError("a source file box changed concurrently")

    outcome = IntakeFileOutcome(created=[], updated=[], moved=[], preserved=[])
    before: dict[int, dict[str, object]] = {}
    touched: list[BoxFile] = []
    now = datetime.now(UTC)
    for value in values:
        normalized = normalize_box_file_reference(value.reference)
        existing = matches_by_reference.get(normalized)
        if existing is not None:
            if existing.archived_at is not None:
                raise BoxFileReferenceConflictError(
                    f"file reference {value.reference!r} is archived in this lot"
                )
            source_box = box if existing.box_id == box.id else boxes_by_id[existing.box_id]
            before[existing.id] = _snapshot(
                db, existing, box=source_box, lot=box.lot_record
            )
            if existing.box_id != box.id:
                if not allow_moves:
                    raise BoxFileReferenceConflictError(
                        f"file reference {value.reference!r} is active in box "
                        f"{source_box.box_number!r}"
                    )
                existing.box_id = box.id
                outcome.moved.append(existing)
            old_details = (
                existing.reference,
                existing.description,
                existing.barcode,
            )
            existing.reference = value.reference
            existing.description = value.description
            existing.barcode = value.barcode
            existing.updated_by_user_id = user.id
            existing.updated_at = now
            if old_details != (
                existing.reference,
                existing.description,
                existing.barcode,
            ) and existing not in outcome.moved:
                outcome.updated.append(existing)
            touched.append(existing)
            continue
        file = BoxFile(
            lot_id=box.lot_id,
            box_id=box.id,
            reference=value.reference,
            description=value.description,
            barcode=value.barcode,
            position=max((item.position for item in target_files), default=0)
            + len(outcome.created)
            + 1,
            created_by_user_id=user.id,
            updated_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(file)
        target_files.append(file)
        outcome.created.append(file)
        touched.append(file)

    supplied = set(normalized_values)
    outcome.preserved = [
        item
        for item in target_files
        if item.archived_at is None
        and item.normalized_reference not in supplied
        and item not in outcome.created
    ]
    active_target = [
        item
        for item in target_files
        if item.archived_at is None and item.box_id == box.id
    ]
    for moved in outcome.moved:
        if moved not in active_target:
            active_target.append(moved)
    archived_target = [
        item
        for item in target_files
        if item.archived_at is not None and item.box_id == box.id
    ]
    orders: dict[int, list[BoxFile]] = {
        box.id: active_target + archived_target,
    }
    for source_box_id in source_box_ids:
        orders[source_box_id] = _active_first(
            [item for item in source_files[source_box_id] if item.box_id == source_box_id]
        )
    _persist_orders(db, orders)
    db.flush()

    for file in touched:
        after = _snapshot(db, file, box=box, lot=box.lot_record)
        previous = before.get(file.id)
        if file in outcome.created:
            event_type = BoxFileEventType.created
        elif file in outcome.moved:
            event_type = BoxFileEventType.moved
        elif file in outcome.updated:
            event_type = BoxFileEventType.updated
        else:
            continue
        _add_event(
            db,
            file=file,
            event_type=event_type,
            before=previous,
            after=after,
            user=user,
            reason=reason,
            metadata={"operation": "intake_reconciliation"},
            now=now,
        )
    db.flush()
    return outcome


def snapshot_request_item_files(
    db: Session,
    *,
    item: BoxRequestItem,
    files: list[BoxFile] | None = None,
    values: list[IntakeFile] | None = None,
) -> list[BoxRequestItemFileSnapshot]:
    """Capture immutable file values for a request item."""
    snapshots: list[BoxRequestItemFileSnapshot] = []
    if files is not None:
        active = sorted(
            (file for file in files if file.archived_at is None),
            key=lambda file: (file.position, file.id),
        )
        for position, file in enumerate(active, start=1):
            snapshots.append(
                BoxRequestItemFileSnapshot(
                    request_item_id=item.id,
                    file_id=file.id,
                    reference=file.reference,
                    description=file.description,
                    barcode=file.barcode,
                    position=position,
                    snapshot_kind=BoxRequestItemFileSnapshotKind.tracked_file,
                )
            )
    elif values is not None:
        for position, value in enumerate(values, start=1):
            snapshots.append(
                BoxRequestItemFileSnapshot(
                    request_item_id=item.id,
                    file_id=None,
                    reference=value.reference,
                    description=value.description,
                    barcode=value.barcode,
                    position=position,
                    snapshot_kind=(
                        BoxRequestItemFileSnapshotKind.legacy_contents
                        if value.legacy
                        else BoxRequestItemFileSnapshotKind.tracked_file
                    ),
                )
            )
    db.add_all(snapshots)
    return snapshots


def create_box_file(
    db: Session,
    *,
    user: User,
    box_id: int,
    reference: str,
    description: str | None = None,
    barcode: str | None = None,
    position: int | None = None,
) -> BoxFile:
    box, lot = _lock_box(db, box_id)
    _check_access(user, box)
    files = _files_for_boxes(db, [box_id])[box_id]
    normalized = normalize_box_file_reference(reference)
    if db.scalar(
        select(BoxFile.id).where(
            BoxFile.lot_id == lot.id,
            BoxFile.normalized_reference == normalized,
        )
    ) is not None:
        raise BoxFileReferenceConflictError(
            f"file reference {reference!r} already exists in lot {lot.name!r}"
        )
    before = {item.id: _snapshot(db, item, box=box, lot=lot) for item in files}
    now = datetime.now(UTC)
    file = BoxFile(
        lot_id=lot.id,
        box_id=box.id,
        reference=reference,
        description=_clean_optional(description),
        barcode=_clean_optional(barcode),
        position=max((item.position for item in files), default=0) + 1,
        created_by_user_id=user.id,
        updated_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(file)
            db.flush()
            orders = {box.id: _insert_at(files + [file], file, position)}
            _persist_orders(db, orders)
            for item in orders[box.id]:
                after = _snapshot(db, item, box=box, lot=lot)
                if item is file:
                    _add_event(
                        db,
                        file=item,
                        event_type=BoxFileEventType.created,
                        before=None,
                        after=after,
                        user=user,
                    )
                elif before[item.id] != after:
                    _add_event(
                        db,
                        file=item,
                        event_type=BoxFileEventType.updated,
                        before=before[item.id],
                        after=after,
                        user=user,
                        metadata={"operation": "position_compacted"},
                    )
            db.flush()
    except IntegrityError as exc:
        raise BoxFileConflictError("file reference or position changed concurrently") from exc
    db.commit()
    db.refresh(file)
    return file


def update_box_file(
    db: Session,
    *,
    user: User,
    file_id: int,
    expected_version: int,
    values: dict[str, object],
    force: bool = False,
    reason: str | None = None,
) -> BoxFile:
    _force_is_valid(user, force, reason)
    preliminary_box_id = _preliminary_box_id(db, file_id)
    box, lot = _lock_box(db, preliminary_box_id)
    file = _lock_file(db, file_id)
    if file.box_id != preliminary_box_id:
        raise BoxFileConflictError("file placement changed concurrently; refresh and retry")
    _check_access(user, box)
    if file.archived_at is not None:
        raise BoxFileConflictError("archived files cannot be modified")
    _check_version(file, expected_version)
    reference_changed = (
        "reference" in values
        and normalize_box_file_reference(str(values["reference"]))
        != file.normalized_reference
    )
    now = datetime.now(UTC)
    cancelled = (
        _guard_reservation(
            db, box=box, user=user, force=force, reason=reason, now=now
        )
        if reference_changed
        else []
    )
    before = _snapshot(db, file, box=box, lot=lot)
    if "reference" in values:
        file.reference = str(values["reference"])
    if "description" in values:
        file.description = _clean_optional(values["description"])  # type: ignore[arg-type]
    if "barcode" in values:
        file.barcode = _clean_optional(values["barcode"])  # type: ignore[arg-type]
    file.updated_by_user_id = user.id
    file.updated_at = now
    try:
        with db.begin_nested():
            db.flush()
            after = _snapshot(db, file, box=box, lot=lot)
            if before != after:
                _add_event(
                    db,
                    file=file,
                    event_type=BoxFileEventType.updated,
                    before=before,
                    after=after,
                    user=user,
                    reason=reason,
                    metadata={
                        "operation": "metadata_update",
                        "admin_override": bool(cancelled),
                        "cancelled_request_ids": cancelled,
                    },
                )
                db.flush()
    except IntegrityError as exc:
        raise BoxFileReferenceConflictError(
            f"file reference already exists in lot {lot.name!r}"
        ) from exc
    db.commit()
    db.refresh(file)
    return file


def move_box_file(
    db: Session,
    *,
    user: User,
    file_id: int,
    target_box_id: int,
    expected_version: int,
    position: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> BoxFile:
    _force_is_valid(user, force, reason)
    preliminary_box_id = _preliminary_box_id(db, file_id)
    box_ids = sorted({preliminary_box_id, target_box_id})
    locked = {box.id: (box, lot) for box, lot in (_lock_box(db, value) for value in box_ids)}
    file = _lock_file(db, file_id)
    if file.box_id != preliminary_box_id:
        raise BoxFileConflictError("file placement changed concurrently; refresh and retry")
    source, source_lot = locked[file.box_id]
    target, target_lot = locked[target_box_id]
    _check_access(user, source)
    _check_access(user, target)
    if source_lot.id != target_lot.id or file.lot_id != target_lot.id:
        raise BoxFileConflictError("files can only move between boxes in the same lot")
    if file.archived_at is not None:
        raise BoxFileConflictError("archived files cannot be moved")
    _check_version(file, expected_version)
    now = datetime.now(UTC)
    cancelled = _guard_reservation(
        db, box=source, user=user, force=force, reason=reason, now=now
    )
    grouped = _files_for_boxes(db, box_ids)
    all_files = [item for items in grouped.values() for item in items]
    before = {
        item.id: _snapshot(
            db,
            item,
            box=source if item.box_id == source.id else target,
            lot=source_lot,
        )
        for item in all_files
    }
    source_order = [item for item in grouped[source.id] if item.id != file.id]
    target_candidates = (
        source_order if source.id == target.id else grouped[target.id]
    )
    target_order = _insert_at(target_candidates + [file], file, position)
    orders = (
        {source.id: target_order}
        if source.id == target.id
        else {source.id: _active_first(source_order), target.id: target_order}
    )
    try:
        with db.begin_nested():
            _persist_orders(db, orders)
            for item in all_files:
                hierarchy = target if item.box_id == target.id else source
                after = _snapshot(db, item, box=hierarchy, lot=source_lot)
                if before[item.id] == after:
                    continue
                _add_event(
                    db,
                    file=item,
                    event_type=(
                        BoxFileEventType.moved
                        if item.id == file.id
                        else BoxFileEventType.updated
                    ),
                    before=before[item.id],
                    after=after,
                    user=user,
                    reason=reason,
                    metadata={
                        "operation": (
                            "file_move" if item.id == file.id else "position_compacted"
                        ),
                        "admin_override": bool(cancelled),
                        "cancelled_request_ids": cancelled,
                    },
                )
            db.flush()
    except IntegrityError as exc:
        raise BoxFileConflictError("file placement changed concurrently") from exc
    db.commit()
    db.refresh(file)
    return file


def archive_box_file(
    db: Session,
    *,
    user: User,
    file_id: int,
    expected_version: int,
    reason: str,
    force: bool = False,
) -> BoxFile:
    _force_is_valid(user, force, reason)
    preliminary_box_id = _preliminary_box_id(db, file_id)
    box, lot = _lock_box(db, preliminary_box_id)
    file = _lock_file(db, file_id)
    if file.box_id != preliminary_box_id:
        raise BoxFileConflictError("file placement changed concurrently; refresh and retry")
    _check_access(user, box)
    _check_version(file, expected_version)
    if file.archived_at is not None:
        raise BoxFileConflictError("file is already archived")
    files = _files_for_boxes(db, [box.id])[box.id]
    before = {item.id: _snapshot(db, item, box=box, lot=lot) for item in files}
    now = datetime.now(UTC)
    cancelled = _guard_reservation(
        db, box=box, user=user, force=force, reason=reason, now=now
    )
    file.archived_at = now
    file.archived_by_user_id = user.id
    file.archive_reason = reason.strip()
    file.updated_by_user_id = user.id
    file.updated_at = now
    try:
        with db.begin_nested():
            order = _active_first(files)
            _persist_orders(db, {box.id: order})
            for item in order:
                after = _snapshot(db, item, box=box, lot=lot)
                if before[item.id] == after:
                    continue
                _add_event(
                    db,
                    file=item,
                    event_type=(
                        BoxFileEventType.archived
                        if item.id == file.id
                        else BoxFileEventType.updated
                    ),
                    before=before[item.id],
                    after=after,
                    user=user,
                    reason=reason,
                    metadata={
                        "operation": (
                            "file_archive"
                            if item.id == file.id
                            else "position_compacted"
                        ),
                        "admin_override": bool(cancelled),
                        "cancelled_request_ids": cancelled,
                    },
                )
            db.flush()
    except IntegrityError as exc:
        raise BoxFileConflictError("file archive changed concurrently") from exc
    db.commit()
    db.refresh(file)
    return file


def restore_box_file(
    db: Session,
    *,
    user: User,
    file_id: int,
    expected_version: int,
    reason: str,
    position: int | None = None,
    force: bool = False,
) -> BoxFile:
    _force_is_valid(user, force, reason)
    preliminary_box_id = _preliminary_box_id(db, file_id)
    box, lot = _lock_box(db, preliminary_box_id)
    file = _lock_file(db, file_id)
    if file.box_id != preliminary_box_id:
        raise BoxFileConflictError("file placement changed concurrently; refresh and retry")
    _check_access(user, box)
    _check_version(file, expected_version)
    if file.archived_at is None:
        raise BoxFileConflictError("file is already active")
    files = _files_for_boxes(db, [box.id])[box.id]
    before = {item.id: _snapshot(db, item, box=box, lot=lot) for item in files}
    now = datetime.now(UTC)
    cancelled = _guard_reservation(
        db, box=box, user=user, force=force, reason=reason, now=now
    )
    file.archived_at = None
    file.archived_by_user_id = None
    file.archive_reason = None
    file.updated_by_user_id = user.id
    file.updated_at = now
    try:
        with db.begin_nested():
            order = _insert_at(files, file, position)
            _persist_orders(db, {box.id: order})
            for item in order:
                after = _snapshot(db, item, box=box, lot=lot)
                if before[item.id] == after:
                    continue
                _add_event(
                    db,
                    file=item,
                    event_type=(
                        BoxFileEventType.restored
                        if item.id == file.id
                        else BoxFileEventType.updated
                    ),
                    before=before[item.id],
                    after=after,
                    user=user,
                    reason=reason,
                    metadata={
                        "operation": (
                            "file_restore"
                            if item.id == file.id
                            else "position_compacted"
                        ),
                        "admin_override": bool(cancelled),
                        "cancelled_request_ids": cancelled,
                    },
                )
            db.flush()
    except IntegrityError as exc:
        raise BoxFileConflictError("file restore changed concurrently") from exc
    db.commit()
    db.refresh(file)
    return file


def emit_inherited_box_file_events(
    db: Session,
    *,
    changes: list[InheritedBoxChange],
    user: User,
    reason: str | None = None,
    metadata: dict[str, object] | None = None,
    now: datetime | None = None,
) -> int:
    """Batch child audit events for parent warehouse/status changes.

    All child rows are fetched in one query and warehouse labels in one
    additional query, regardless of the number of boxes or files.
    """
    if not changes:
        return 0
    changes_by_box: dict[int, list[InheritedBoxChange]] = {}
    for change in changes:
        changes_by_box.setdefault(change.box_id, []).append(change)
    rows = db.execute(
        _view_stmt()
        .where(BoxFile.box_id.in_(changes_by_box))
        .order_by(BoxFile.box_id, BoxFile.position)
        .with_for_update(of=BoxFile)
    ).all()
    warehouse_ids = {
        warehouse_id
        for change in changes
        for warehouse_id in (change.from_warehouse_id, change.to_warehouse_id)
    }
    warehouse_names = dict(
        db.execute(
            select(Warehouse.id, Warehouse.name).where(Warehouse.id.in_(warehouse_ids))
        ).all()
    )
    occurred_at = now or datetime.now(UTC)
    count = 0
    for row in rows:
        current = _json_snapshot(_view(row))
        for change in changes_by_box[int(current["box_id"])]:
            before = dict(current)
            after = dict(current)
            before.update(
                warehouse_id=change.from_warehouse_id,
                warehouse=warehouse_names.get(change.from_warehouse_id),
                status=change.from_status.value,
            )
            after.update(
                warehouse_id=change.to_warehouse_id,
                warehouse=warehouse_names.get(change.to_warehouse_id),
                status=change.to_status.value,
            )
            db.add(
                BoxFileEvent(
                    file_id=int(current["id"]),
                    event_type=change.event_type,
                    before_snapshot=before,
                    after_snapshot=after,
                    actor_user_id=user.id,
                    reason=reason,
                    event_metadata={
                        "operation": "inherited_box_change",
                        **(metadata or {}),
                    },
                    occurred_at=occurred_at,
                )
            )
            count += 1
    return count


__all__ = [
    "BoxFileAccessError",
    "BoxFileConflictError",
    "BoxFileNotFoundError",
    "BoxFileReferenceConflictError",
    "BoxFileRuleError",
    "BoxFileVersionConflictError",
    "InheritedBoxChange",
    "IntakeFile",
    "IntakeFileOutcome",
    "archive_box_file",
    "create_box_file",
    "emit_inherited_box_file_events",
    "file_integrity_report",
    "get_box_file",
    "intake_file_values",
    "legacy_intake_reference",
    "list_box_file_events",
    "list_box_files",
    "move_box_file",
    "reconcile_intake_box_files",
    "restore_box_file",
    "snapshot_request_item_files",
    "split_legacy_contents",
    "update_box_file",
]
