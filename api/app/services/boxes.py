"""Domain service for box mutations.

Centralises status-transition validation, timestamp bookkeeping and audit-log
writes so routers stay thin and we never forget to write a `box_events` row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.boxes import Box, BoxEvent, BoxEventType, BoxStatus
from app.models.notifications import RequestNotificationKind
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestItem,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.acl import can_access
from app.services.request_notifications import enqueue_request_event
from app.services.warehouses import WarehouseRuleError, ensure_active_warehouse

# Valid forward transitions: a box walks the chain one step at a time. The
# physical reality is:
#   received        - closed, full, available for use
#   processing      - open, being worked on, still has stuff
#   incomplete      - open, empty of stuff, but pages from it are still
#                     being processed elsewhere (data entry, QC, ...)
#   ready_to_return - closed, packaged for pickup
#   returned        - terminal, left the warehouse
# Skip-ahead transitions are intentionally disallowed without ``force=true``
# so we never lose intermediate audit rows. Admins still get the bypass via
# the force flag enforced in the router.
_ALLOWED_TRANSITIONS: dict[BoxStatus, set[BoxStatus]] = {
    BoxStatus.quarantined: {BoxStatus.received},
    BoxStatus.received: {BoxStatus.processing},
    BoxStatus.processing: {BoxStatus.incomplete},
    BoxStatus.incomplete: {BoxStatus.ready_to_return},
    BoxStatus.ready_to_return: {BoxStatus.returned},
    BoxStatus.returned: set(),
}


class BoxRuleError(Exception):
    """A domain rule was violated (bad transition, missing warehouse, etc.).

    Routers translate this to HTTPException at the edge so business rules
    stay HTTP-agnostic and bulk loops can collect per-row reasons without
    fighting FastAPI's exception handlers.
    """


class BoxConflictError(BoxRuleError):
    """A uniqueness violation for ``(lot, box_number)``.

    Box numbers are unique only within a lot; this is raised when an
    operator tries to add the same number twice in the same lot. Same
    number under a different lot is allowed and will not trigger this.
    """


def normalize_box_number(raw: str) -> str:
    """Canonical form for box numbers: numeric, zero-padded to 3 digits.

    Strips whitespace, requires the remaining string to be purely digits
    and zero-pads to a minimum of 3 characters. Values already longer
    than 3 digits (``"1234"``) round-trip unchanged. Empty or non-numeric
    input raises ``BoxRuleError`` so callers (both the schema validator
    and the XLSX import) get a single source of truth for the rule.
    """
    cleaned = raw.strip() if raw is not None else ""
    if not cleaned:
        raise BoxRuleError("box_number is required")
    if not cleaned.isdigit():
        raise BoxRuleError("box_number must be numeric")
    return cleaned.zfill(3)


class BoxAccessError(BoxRuleError):
    """The caller does not have ACL access to the warehouse involved."""


def _ensure_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    try:
        return ensure_active_warehouse(db, warehouse_id)
    except WarehouseRuleError as exc:
        raise BoxRuleError(str(exc)) from exc


def _referencing_request_ids(db: Session, box_id: int) -> list[int]:
    return list(
        db.scalars(
            select(BoxRequestItem.request_id)
            .where(BoxRequestItem.box_id == box_id)
            .distinct()
            .order_by(BoxRequestItem.request_id)
        ).all()
    )


def _active_return_request_stmt(box_id: int) -> Select[tuple[BoxRequest]]:
    return (
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
        .with_for_update()
    )


def _active_return_requests(db: Session, box_id: int) -> list[BoxRequest]:
    return list(db.scalars(_active_return_request_stmt(box_id)).all())


def _cancel_active_returns(
    db: Session,
    *,
    box_id: int,
    user: User,
    reason: str,
    now: datetime,
) -> set[int]:
    cancelled: set[int] = set()
    note = (
        f"Cancelled by admin override affecting box #{box_id}. "
        f"Reason: {reason.strip()}"
    )
    for request in _active_return_requests(db, box_id):
        old_status = request.status
        request.status = BoxRequestStatus.cancelled
        request.cancellation_reason = note
        request.version += 1
        request.updated_at = now
        db.add(
            BoxRequestEvent(
                request_id=request.id,
                event_type=BoxRequestEventType.cancelled,
                from_status=old_status,
                to_status=BoxRequestStatus.cancelled,
                user_id=user.id,
                note=note,
                occurred_at=now,
                event_metadata={
                    "admin_override": True,
                    "cancelled_reservation": True,
                    "box_id": box_id,
                    "override_reason": reason.strip(),
                    "operation": "reservation_cancelled",
                },
            )
        )
        enqueue_request_event(
            db,
            request=request,
            kind=RequestNotificationKind.reservation_cancelled,
            event_token=f"reservation-cancelled:{request.version}",
            detail=note,
        )
        cancelled.add(request.id)
    return cancelled


def create_box(
    db: Session,
    *,
    user: User,
    box_number: str,
    lot: str,
    warehouse_id: int,
    contents: str | None = None,
    note: str | None = None,
    initial_status: BoxStatus = BoxStatus.received,
    commit: bool = True,
) -> Box:
    _ensure_warehouse(db, warehouse_id)
    if not can_access(user, warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {warehouse_id}"
        )
    cleaned_number = normalize_box_number(box_number)
    cleaned_lot = lot.strip()
    if not cleaned_lot:
        raise BoxRuleError("lot is required")
    cleaned_contents: str | None = None
    if contents is not None:
        stripped = contents.strip()
        cleaned_contents = stripped or None
    existing = db.scalar(
        select(Box).where(
            Box.box_number == cleaned_number,
            Box.lot == cleaned_lot,
        )
    )
    if existing is not None:
        raise BoxConflictError(
            f"box {cleaned_number!r} already exists in lot {cleaned_lot!r}"
        )
    now = datetime.now(UTC)
    box = Box(
        box_number=cleaned_number,
        lot=cleaned_lot,
        contents=cleaned_contents,
        current_warehouse_id=warehouse_id,
        status=initial_status,
        received_at=now,
        updated_by_user_id=user.id,
    )
    db.add(box)
    db.flush()
    db.add(
        BoxEvent(
            box_id=box.id,
            warehouse_id=warehouse_id,
            event_type=BoxEventType.created,
            from_status=None,
            to_status=initial_status,
            from_warehouse_id=None,
            to_warehouse_id=warehouse_id,
            occurred_at=now,
            user_id=user.id,
            note=note,
        )
    )
    if commit:
        db.commit()
        db.refresh(box)
    else:
        db.flush()
    return box


def restore_archived_box(
    db: Session,
    *,
    user: User,
    box_number: str,
    lot: str,
    warehouse_id: int,
    contents: str | None = None,
    note: str | None = None,
    restored_status: BoxStatus = BoxStatus.received,
    commit: bool = True,
) -> Box | None:
    """Reactivate an archived identity, returning ``None`` when none exists."""
    _ensure_warehouse(db, warehouse_id)
    if not can_access(user, warehouse_id):
        raise BoxAccessError(f"no access to warehouse {warehouse_id}")
    cleaned_number = normalize_box_number(box_number)
    cleaned_lot = lot.strip()
    if not cleaned_lot:
        raise BoxRuleError("lot is required")
    box = db.scalar(
        select(Box)
        .where(
            Box.box_number == cleaned_number,
            Box.lot == cleaned_lot,
        )
        .with_for_update()
    )
    if box is None:
        return None
    if box.archived_at is None:
        raise BoxConflictError(
            f"box {cleaned_number!r} already exists in lot {cleaned_lot!r}"
        )
    if not can_access(user, box.current_warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {box.current_warehouse_id}"
        )
    if _active_return_requests(db, box.id):
        raise BoxConflictError(
            f"archived box {box.id} still has an active return reservation"
        )

    now = datetime.now(UTC)
    old_status = box.status
    old_warehouse_id = box.current_warehouse_id
    previous_archive_reason = box.archive_reason
    cleaned_contents = (contents or "").strip() or None
    box.current_warehouse_id = warehouse_id
    box.status = restored_status
    box.contents = cleaned_contents
    box.received_at = now
    box.processing_completed_at = None
    box.returned_at = None
    box.archived_at = None
    box.archived_by_user_id = None
    box.archive_reason = None
    box.updated_by_user_id = user.id
    box.updated_at = now

    context = (note or "Restored during XLSX import.").strip()
    if previous_archive_reason:
        context = (
            f"{context} Previous archive reason: {previous_archive_reason}"
        )
    db.add(
        BoxEvent(
            box_id=box.id,
            warehouse_id=warehouse_id,
            event_type=BoxEventType.restored,
            from_status=old_status,
            to_status=restored_status,
            from_warehouse_id=old_warehouse_id,
            to_warehouse_id=warehouse_id,
            occurred_at=now,
            user_id=user.id,
            note=context,
        )
    )
    if commit:
        db.commit()
        db.refresh(box)
    else:
        db.flush()
    return box


def _audit_note(note: str | None, *, force: bool) -> str | None:
    """Tag override notes so the box history clearly shows the bypass."""
    if not force:
        return note
    base = (note or "").strip()
    return f"[admin override] {base}" if base else "[admin override]"


def update_box(
    db: Session,
    *,
    user: User,
    box: Box,
    new_status: BoxStatus | None = None,
    new_warehouse_id: int | None = None,
    new_lot: str | None = None,
    new_contents: str | None = None,
    note: str | None = None,
    force: bool = False,
    commit: bool = True,
    cancelled_request_ids: set[int] | None = None,
    skip_request_guards: bool = False,
) -> Box:
    now = datetime.now(UTC)
    events: list[BoxEvent] = []
    audit_note = _audit_note(note, force=force)
    metadata_changed = False

    # ACL: the caller must have access to the box's *current* warehouse to
    # touch it at all, and to the *target* warehouse if they're moving it.
    # Admins bypass via can_access.
    if not can_access(user, box.current_warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {box.current_warehouse_id}"
        )
    _ensure_warehouse(db, box.current_warehouse_id)
    if box.archived_at is not None:
        raise BoxConflictError("archived boxes cannot be modified")

    moving = (
        new_warehouse_id is not None
        and new_warehouse_id != box.current_warehouse_id
    )
    changing_status = new_status is not None and new_status != box.status
    if (
        changing_status
        and (box.status == BoxStatus.quarantined or new_status == BoxStatus.quarantined)
        and user.role != UserRole.admin
    ):
        raise BoxAccessError("quarantine changes require admin role")
    request_ids: list[int] = []
    cancelled: set[int] = set()
    if moving and new_warehouse_id is not None:
        _ensure_warehouse(db, new_warehouse_id)
        if not can_access(user, new_warehouse_id):
            raise BoxAccessError(
                f"no access to warehouse {new_warehouse_id}"
            )
    if force and (moving or changing_status) and not (note or "").strip():
        raise BoxRuleError("a reason is required for an admin override")
    if not skip_request_guards and (moving or changing_status):
        request_ids = _referencing_request_ids(db, box.id)
    if not skip_request_guards and moving:
        if request_ids and not force:
            joined = ", ".join(f"#{request_id}" for request_id in request_ids)
            raise BoxConflictError(
                f"box is linked to request(s) {joined}; use an admin override to move it"
            )
    if not skip_request_guards and (moving or changing_status):
        active_returns = _active_return_requests(db, box.id)
        if active_returns and not force:
            joined = ", ".join(f"#{request.id}" for request in active_returns)
            raise BoxConflictError(
                f"box is reserved by active return request(s) {joined}"
            )
        if active_returns and force:
            cancelled = _cancel_active_returns(
                db,
                box_id=box.id,
                user=user,
                reason=(note or "").strip(),
                now=now,
            )
            if cancelled_request_ids is not None:
                cancelled_request_ids.update(cancelled)

    if new_lot is not None:
        cleaned_lot = new_lot.strip()
        if not cleaned_lot:
            raise BoxRuleError("lot is required")
        if cleaned_lot != box.lot:
            box.lot = cleaned_lot
            metadata_changed = True

    if new_contents is not None:
        # Empty string clears the optional descriptor.
        stripped = new_contents.strip()
        next_contents = stripped or None
        if next_contents != box.contents:
            box.contents = next_contents
            metadata_changed = True

    if new_warehouse_id is not None and new_warehouse_id != box.current_warehouse_id:
        _ensure_warehouse(db, new_warehouse_id)
        if not can_access(user, new_warehouse_id):
            raise BoxAccessError(
                f"no access to warehouse {new_warehouse_id}"
            )
        if box.status == BoxStatus.returned and not force:
            raise BoxRuleError("cannot move a returned box")
        events.append(
            BoxEvent(
                box_id=box.id,
                warehouse_id=new_warehouse_id,
                event_type=BoxEventType.moved,
                from_status=box.status,
                to_status=box.status,
                from_warehouse_id=box.current_warehouse_id,
                to_warehouse_id=new_warehouse_id,
                occurred_at=now,
                user_id=user.id,
                note=audit_note,
                event_metadata={
                    "admin_override": force,
                    "override_reason": (note or "").strip() or None,
                    "operation": "forced_move" if force else "move",
                    "linked_request_ids": request_ids,
                    "cancelled_request_ids": sorted(cancelled),
                },
            )
        )
        box.current_warehouse_id = new_warehouse_id

    if new_status is not None and new_status != box.status:
        allowed = _ALLOWED_TRANSITIONS.get(box.status, set())
        if new_status not in allowed and not force:
            raise BoxRuleError(
                f"cannot transition from {box.status.value} to {new_status.value}"
            )
        old_status = box.status
        box.status = new_status
        if new_status == BoxStatus.returned:
            box.returned_at = now
        events.append(
            BoxEvent(
                box_id=box.id,
                warehouse_id=box.current_warehouse_id,
                event_type=(
                    BoxEventType.returned
                    if new_status == BoxStatus.returned
                    else BoxEventType.status_changed
                ),
                from_status=old_status,
                to_status=new_status,
                from_warehouse_id=box.current_warehouse_id,
                to_warehouse_id=box.current_warehouse_id,
                occurred_at=now,
                user_id=user.id,
                note=audit_note,
                event_metadata={
                    "admin_override": force,
                    "override_reason": (note or "").strip() or None,
                    "operation": "forced_status_change" if force else "status_change",
                    "linked_request_ids": request_ids,
                    "cancelled_request_ids": sorted(cancelled),
                },
            )
        )

    if not events and not metadata_changed:
        return box

    box.updated_by_user_id = user.id
    box.updated_at = now
    for ev in events:
        db.add(ev)
    if commit:
        db.commit()
        db.refresh(box)
    else:
        db.flush()
    return box


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


@dataclass
class BulkSkipEntry:
    box_id: int
    box_number: str
    reason: str


@dataclass
class BulkOutcome:
    updated: list[Box] = field(default_factory=list)
    skipped: list[BulkSkipEntry] = field(default_factory=list)
    cancelled_request_ids: set[int] = field(default_factory=set)


def bulk_update_boxes(
    db: Session,
    *,
    user: User,
    box_ids: list[int],
    new_warehouse_id: int | None = None,
    new_status: BoxStatus | None = None,
    note: str | None = None,
    force: bool = False,
) -> BulkOutcome:
    """Apply the same change to many boxes, best-effort.

    Eligible rows are updated; ineligible rows (returned boxes, illegal
    transitions, missing IDs) are reported in `skipped` with a human-readable
    reason. The caller is responsible for publishing SSE/alert side-effects
    once after the loop. Pass `force=True` to bypass transition/move rules
    (admin only; the router enforces that).
    """
    if new_warehouse_id is None and new_status is None:
        raise BoxRuleError("must specify warehouse_id or status")

    outcome = BulkOutcome()
    seen: set[int] = set()
    for box_id in box_ids:
        if box_id in seen:
            continue
        seen.add(box_id)

        box = db.get(Box, box_id)
        if box is None:
            outcome.skipped.append(
                BulkSkipEntry(box_id=box_id, box_number="", reason="box not found")
            )
            continue
        try:
            updated = update_box(
                db,
                user=user,
                box=box,
                new_status=new_status,
                new_warehouse_id=new_warehouse_id,
                note=note,
                force=force,
                cancelled_request_ids=outcome.cancelled_request_ids,
            )
        except BoxRuleError as exc:
            outcome.skipped.append(
                BulkSkipEntry(
                    box_id=box.id, box_number=box.box_number, reason=str(exc)
                )
            )
            continue
        outcome.updated.append(updated)

    return outcome


# ---------------------------------------------------------------------------
# Delete operations (admin only)
# ---------------------------------------------------------------------------


@dataclass
class BulkDeleteOutcome:
    deleted_ids: list[int] = field(default_factory=list)
    archived_ids: list[int] = field(default_factory=list)
    cancelled_request_ids: set[int] = field(default_factory=set)
    affected_warehouse_ids: set[int] = field(default_factory=set)
    skipped: list[BulkSkipEntry] = field(default_factory=list)


@dataclass(frozen=True)
class DeleteOutcome:
    box_id: int
    warehouse_id: int
    archived: bool
    cancelled_request_ids: set[int]


def delete_box(
    db: Session,
    *,
    user: User,
    box: Box,
    force: bool = False,
    reason: str | None = None,
    commit: bool = True,
) -> DeleteOutcome:
    """Delete unreferenced inventory or archive a force-deleted linked box."""
    warehouse_id = box.current_warehouse_id
    _ensure_warehouse(db, warehouse_id)
    request_ids = _referencing_request_ids(db, box.id)
    cancelled_request_ids: set[int] = set()
    if request_ids:
        joined = ", ".join(f"#{request_id}" for request_id in request_ids)
        if not force:
            raise BoxConflictError(
                f"box is linked to request(s) {joined}; force delete will archive it"
            )
        cleaned_reason = (reason or "").strip()
        if not cleaned_reason:
            raise BoxRuleError("a reason is required to archive a linked box")
        now = datetime.now(UTC)
        cancelled_request_ids = _cancel_active_returns(
            db,
            box_id=box.id,
            user=user,
            reason=cleaned_reason,
            now=now,
        )
        if box.archived_at is None:
            box.archived_at = now
            box.archived_by_user_id = user.id
            box.archive_reason = cleaned_reason
            box.updated_by_user_id = user.id
            box.updated_at = now
            db.add(
                BoxEvent(
                    box_id=box.id,
                    warehouse_id=warehouse_id,
                    event_type=BoxEventType.archived,
                    from_status=box.status,
                    to_status=box.status,
                    from_warehouse_id=warehouse_id,
                    to_warehouse_id=warehouse_id,
                    occurred_at=now,
                    user_id=user.id,
                    note=f"[admin override] {cleaned_reason}",
                    event_metadata={
                        "admin_override": True,
                        "override_reason": cleaned_reason,
                        "operation": "linked_box_archive",
                        "linked_request_ids": request_ids,
                        "cancelled_request_ids": sorted(cancelled_request_ids),
                    },
                )
            )
        archived = True
    else:
        db.delete(box)
        archived = False
    if commit:
        db.commit()
    else:
        db.flush()
    return DeleteOutcome(
        box_id=box.id,
        warehouse_id=warehouse_id,
        archived=archived,
        cancelled_request_ids=cancelled_request_ids,
    )


def bulk_delete_boxes(
    db: Session,
    *,
    user: User,
    box_ids: list[int],
    force: bool = False,
    reason: str | None = None,
) -> BulkDeleteOutcome:
    """Delete or archive many boxes in one transaction."""
    if force and not (reason or "").strip():
        raise BoxRuleError("a reason is required for an admin override")
    outcome = BulkDeleteOutcome()
    seen: set[int] = set()
    for box_id in box_ids:
        if box_id in seen:
            continue
        seen.add(box_id)

        box = db.get(Box, box_id)
        if box is None:
            outcome.skipped.append(
                BulkSkipEntry(box_id=box_id, box_number="", reason="box not found")
            )
            continue
        try:
            result = delete_box(
                db,
                user=user,
                box=box,
                force=force,
                reason=reason,
                commit=False,
            )
        except BoxRuleError as exc:
            outcome.skipped.append(
                BulkSkipEntry(
                    box_id=box.id,
                    box_number=box.box_number,
                    reason=str(exc),
                )
            )
            continue
        outcome.affected_warehouse_ids.add(result.warehouse_id)
        outcome.cancelled_request_ids.update(result.cancelled_request_ids)
        if result.archived:
            outcome.archived_ids.append(result.box_id)
        else:
            outcome.deleted_ids.append(result.box_id)

    if outcome.deleted_ids or outcome.archived_ids:
        db.commit()
    return outcome
