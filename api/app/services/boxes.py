"""Domain service for box mutations.

Centralises status-transition validation, timestamp bookkeeping and audit-log
writes so routers stay thin and we never forget to write a `box_events` row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.boxes import Box, BoxEvent, BoxEventType, BoxStatus
from app.models.lots import Lot, LotEvent, LotEventType
from app.models.notifications import RequestNotificationKind
from app.models.pallets import Pallet, PalletEvent, PalletEventType
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
from app.services.lots import LotRuleError, lock_lots_exclusively, resolve_lot
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


def set_box_pallet_assignment(
    db: Session,
    *,
    user: User,
    box: Box,
    target_pallet: Pallet | None,
    reason: str,
    expected_lot_id: int | None = None,
    expected_warehouse_id: int | None = None,
    metadata: dict[str, object] | None = None,
    record_pallet_event: bool = True,
) -> int | None:
    """Change one current pallet link and append immutable box audit events."""
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise BoxRuleError("a reason is required to change pallet assignment")
    lot_id = expected_lot_id if expected_lot_id is not None else box.lot_id
    warehouse_id = (
        expected_warehouse_id
        if expected_warehouse_id is not None
        else box.current_warehouse_id
    )
    if not can_access(user, box.current_warehouse_id) or not can_access(
        user, warehouse_id
    ):
        raise BoxAccessError("warehouse access denied")
    if target_pallet is not None:
        if not target_pallet.is_active:
            raise BoxConflictError("archived pallets cannot receive boxes")
        if target_pallet.lot_id != lot_id:
            raise BoxConflictError("box and pallet must belong to the same lot")
        if target_pallet.current_warehouse_id != warehouse_id:
            raise BoxConflictError("box and pallet must be in the same warehouse")
        if not can_access(user, target_pallet.current_warehouse_id):
            raise BoxAccessError("warehouse access denied")
        if box.archived_at is not None:
            raise BoxConflictError("archived boxes cannot be assigned")
    source_pallet_id = box.pallet_id
    target_pallet_id = target_pallet.id if target_pallet is not None else None
    if source_pallet_id == target_pallet_id:
        return source_pallet_id
    now = datetime.now(UTC)
    common_metadata = {
        "from_pallet_id": source_pallet_id,
        "to_pallet_id": target_pallet_id,
        **(metadata or {}),
    }
    if source_pallet_id is not None:
        db.add(
            BoxEvent(
                box_id=box.id,
                warehouse_id=box.current_warehouse_id,
                event_type=BoxEventType.pallet_unassigned,
                from_status=box.status,
                to_status=box.status,
                from_warehouse_id=box.current_warehouse_id,
                to_warehouse_id=warehouse_id,
                occurred_at=now,
                user_id=user.id,
                note=cleaned_reason,
                event_metadata=common_metadata,
            )
        )
        if record_pallet_event:
            db.add(
                PalletEvent(
                    pallet_id=source_pallet_id,
                    event_type=PalletEventType.boxes_unassigned,
                    actor_user_id=user.id,
                    reason=cleaned_reason,
                    occurred_at=now,
                    event_metadata={
                        **common_metadata,
                        "box_ids": [box.id],
                        "box_count": 1,
                    },
                )
            )
    box.pallet_id = target_pallet_id
    if target_pallet_id is not None:
        db.add(
            BoxEvent(
                box_id=box.id,
                warehouse_id=warehouse_id,
                event_type=BoxEventType.pallet_assigned,
                from_status=box.status,
                to_status=box.status,
                from_warehouse_id=box.current_warehouse_id,
                to_warehouse_id=warehouse_id,
                occurred_at=now,
                user_id=user.id,
                note=cleaned_reason,
                event_metadata=common_metadata,
            )
        )
        if record_pallet_event:
            db.add(
                PalletEvent(
                    pallet_id=target_pallet_id,
                    event_type=PalletEventType.boxes_assigned,
                    actor_user_id=user.id,
                    reason=cleaned_reason,
                    occurred_at=now,
                    event_metadata={
                        **common_metadata,
                        "box_ids": [box.id],
                        "box_count": 1,
                    },
                )
            )
    box.updated_by_user_id = user.id
    box.updated_at = now
    if source_pallet_id is not None and source_pallet_id != target_pallet_id:
        source_pallet = db.get(Pallet, source_pallet_id)
        if source_pallet is not None and source_pallet.is_active:
            remaining = int(
                db.scalar(
                    select(func.count(Box.id)).where(
                        Box.pallet_id == source_pallet_id
                    )
                )
                or 0
            )
            if remaining == 0:
                source_pallet.is_active = False
                source_pallet.archived_at = now
                source_pallet.archived_by_user_id = user.id
                source_pallet.archive_reason = (
                    f"Automatically archived after its final box was removed: "
                    f"{cleaned_reason}"
                )
                source_pallet.updated_by_user_id = user.id
                source_pallet.updated_at = now
                db.add(
                    PalletEvent(
                        pallet_id=source_pallet.id,
                        event_type=PalletEventType.archived,
                        old_pallet_number=source_pallet.pallet_number,
                        actor_user_id=user.id,
                        reason=cleaned_reason,
                        occurred_at=now,
                        event_metadata={
                            **common_metadata,
                            "operation": "automatic_empty_pallet_cleanup",
                            "trigger_box_id": box.id,
                            "remaining_box_count": 0,
                        },
                    )
                )
    return source_pallet_id


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
    lot: str | None = None,
    lot_id: int | None = None,
    warehouse_id: int,
    pallet_number: str | None = None,
    pallet_id: int | None = None,
    contents: str | None = None,
    note: str | None = None,
    initial_status: BoxStatus = BoxStatus.received,
    legacy_allow_unassigned: bool = False,
    receipt_authorized_pallet_creation: bool = False,
    commit: bool = True,
) -> Box:
    _ensure_warehouse(db, warehouse_id)
    if not can_access(user, warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {warehouse_id}"
        )
    cleaned_number = normalize_box_number(box_number)
    try:
        lot_record = resolve_lot(
            db,
            user=user,
            lot=lot,
            lot_id=lot_id,
            warehouse_id=warehouse_id,
        )
    except LotRuleError as exc:
        raise BoxRuleError(str(exc)) from exc
    cleaned_contents: str | None = None
    if contents is not None:
        stripped = contents.strip()
        cleaned_contents = stripped or None
    existing = db.scalar(
        select(Box).where(
            Box.box_number == cleaned_number,
            Box.lot_id == lot_record.id,
        )
    )
    if existing is not None:
        raise BoxConflictError(
            f"box {cleaned_number!r} already exists in lot {lot_record.name!r}"
        )
    target_pallet: Pallet | None = None
    if pallet_number is None:
        if not legacy_allow_unassigned:
            raise BoxRuleError("pallet_number is required for new box receipts")
    else:
        from app.services.pallets import (
            PalletConflictError,
            PalletRuleError,
            resolve_or_create_active_pallet,
        )

        try:
            target_pallet = resolve_or_create_active_pallet(
                db,
                user=user,
                lot_id=lot_record.id,
                warehouse_id=warehouse_id,
                pallet_number=pallet_number,
                pallet_id=pallet_id,
                receipt_creation_authorized=receipt_authorized_pallet_creation,
            )
        except PalletConflictError as exc:
            raise BoxConflictError(str(exc)) from exc
        except PalletRuleError as exc:
            raise BoxRuleError(str(exc)) from exc
    now = datetime.now(UTC)
    box = Box(
        box_number=cleaned_number,
        lot_id=lot_record.id,
        contents=cleaned_contents,
        current_warehouse_id=warehouse_id,
        status=initial_status,
        received_at=now,
        updated_by_user_id=user.id,
    )
    try:
        with db.begin_nested():
            db.add(box)
            db.flush()
            if target_pallet is not None:
                set_box_pallet_assignment(
                    db,
                    user=user,
                    box=box,
                    target_pallet=target_pallet,
                    reason=note or "Assigned during box receipt.",
                    metadata={"operation": "box_receipt"},
                )
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
                    event_metadata={
                        "lot_id": lot_record.id,
                        "pallet_id": target_pallet.id if target_pallet else None,
                        "pallet_number": (
                            target_pallet.pallet_number if target_pallet else None
                        ),
                    },
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise BoxConflictError(
            f"box {cleaned_number!r} already exists in lot {lot_record.name!r}"
        ) from exc
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
    lot: str | None = None,
    lot_id: int | None = None,
    warehouse_id: int,
    pallet_number: str | None = None,
    pallet_id: int | None = None,
    contents: str | None = None,
    note: str | None = None,
    restored_status: BoxStatus = BoxStatus.received,
    legacy_allow_unassigned: bool = False,
    commit: bool = True,
) -> Box | None:
    """Reactivate an archived identity, returning ``None`` when none exists."""
    _ensure_warehouse(db, warehouse_id)
    if not can_access(user, warehouse_id):
        raise BoxAccessError(f"no access to warehouse {warehouse_id}")
    cleaned_number = normalize_box_number(box_number)
    try:
        lot_record = resolve_lot(
            db,
            user=user,
            lot=lot,
            lot_id=lot_id,
            warehouse_id=warehouse_id,
        )
    except LotRuleError as exc:
        raise BoxRuleError(str(exc)) from exc
    box = db.scalar(
        select(Box)
        .where(
            Box.box_number == cleaned_number,
            Box.lot_id == lot_record.id,
        )
        .with_for_update(of=Box)
    )
    if box is None:
        return None
    if box.archived_at is None:
        raise BoxConflictError(
            f"box {cleaned_number!r} already exists in lot {lot_record.name!r}"
        )
    if not can_access(user, box.current_warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {box.current_warehouse_id}"
        )
    if _active_return_requests(db, box.id):
        raise BoxConflictError(
            f"archived box {box.id} still has an active return reservation"
        )
    target_pallet: Pallet | None = None
    if pallet_number is None:
        if not legacy_allow_unassigned:
            raise BoxRuleError("pallet_number is required for restored box receipts")
    else:
        from app.services.pallets import (
            PalletConflictError,
            PalletRuleError,
            resolve_or_create_active_pallet,
        )

        try:
            target_pallet = resolve_or_create_active_pallet(
                db,
                user=user,
                lot_id=lot_record.id,
                warehouse_id=warehouse_id,
                pallet_number=pallet_number,
                pallet_id=pallet_id,
            )
        except PalletConflictError as exc:
            raise BoxConflictError(str(exc)) from exc
        except PalletRuleError as exc:
            raise BoxRuleError(str(exc)) from exc

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

    if target_pallet is not None:
        set_box_pallet_assignment(
            db,
            user=user,
            box=box,
            target_pallet=target_pallet,
            reason=note or "Assigned during box restoration.",
            expected_warehouse_id=warehouse_id,
            metadata={"operation": "box_restore_receipt"},
        )
    elif box.pallet_id is not None:
        pallet = db.get(Pallet, box.pallet_id)
        if (
            pallet is None
            or not pallet.is_active
            or pallet.lot_id != box.lot_id
            or pallet.current_warehouse_id != warehouse_id
        ):
            set_box_pallet_assignment(
                db,
                user=user,
                box=box,
                target_pallet=None,
                reason=note or "Detached incompatible pallet during box restoration.",
                expected_warehouse_id=warehouse_id,
                metadata={"operation": "restore_incompatible_pallet_detach"},
            )

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


@dataclass(frozen=True)
class _RequestReturnMove:
    request_id: int
    source_warehouse_id: int
    target_warehouse_id: int

    def metadata(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "source_warehouse_id": self.source_warehouse_id,
            "target_warehouse_id": self.target_warehouse_id,
            "request_workflow": True,
        }


def update_box(
    db: Session,
    *,
    user: User,
    box: Box,
    new_status: BoxStatus | None = None,
    new_warehouse_id: int | None = None,
    new_contents: str | None = None,
    note: str | None = None,
    force: bool = False,
    commit: bool = True,
    cancelled_request_ids: set[int] | None = None,
    target_pallet_id: int | None = None,
    detach_pallet: bool = False,
    preserve_pallet_on_move: bool = False,
    additional_event_metadata: dict[str, object] | None = None,
    skip_request_guards: bool = False,
    _request_return_move: _RequestReturnMove | None = None,
) -> Box:
    now = datetime.now(UTC)
    events: list[BoxEvent] = []
    audit_note = _audit_note(note, force=force)
    metadata_changed = False
    if force and user.role != UserRole.admin:
        raise BoxAccessError("force override requires admin role")
    if force and not (note or "").strip():
        raise BoxRuleError("a reason is required for an admin override")
    if _request_return_move is not None and (
        box.current_warehouse_id != _request_return_move.source_warehouse_id
        or new_warehouse_id != _request_return_move.target_warehouse_id
        or new_status != BoxStatus.returned
        or not skip_request_guards
    ):
        raise BoxRuleError("invalid internal return request move")
    request_move_metadata = (
        _request_return_move.metadata()
        if _request_return_move is not None
        else {}
    )

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
        if (
            _request_return_move is None
            and not can_access(user, new_warehouse_id)
        ):
            raise BoxAccessError(
                f"no access to warehouse {new_warehouse_id}"
            )
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

    effective_warehouse_id = (
        new_warehouse_id if moving and new_warehouse_id is not None else box.current_warehouse_id
    )
    if target_pallet_id is not None:
        target_pallet = db.scalar(
            select(Pallet)
            .where(Pallet.id == target_pallet_id)
            .with_for_update(of=Pallet)
        )
        if target_pallet is None:
            raise BoxConflictError("target pallet not found")
        source_pallet_id = set_box_pallet_assignment(
            db,
            user=user,
            box=box,
            target_pallet=target_pallet,
            reason=note or "Changed pallet during box update.",
            expected_warehouse_id=effective_warehouse_id,
            metadata={"operation": "box_update_pallet_assignment"},
        )
        metadata_changed = source_pallet_id != target_pallet.id
    elif detach_pallet or (moving and box.pallet_id is not None and not preserve_pallet_on_move):
        source_pallet_id = set_box_pallet_assignment(
            db,
            user=user,
            box=box,
            target_pallet=None,
            reason=note or "Detached pallet during box warehouse move.",
            expected_warehouse_id=box.current_warehouse_id,
            metadata={"operation": "box_update_pallet_detach"},
        )
        metadata_changed = source_pallet_id is not None

    if new_contents is not None:
        # Empty string clears the optional descriptor.
        stripped = new_contents.strip()
        next_contents = stripped or None
        if next_contents != box.contents:
            box.contents = next_contents
            metadata_changed = True

    if new_warehouse_id is not None and new_warehouse_id != box.current_warehouse_id:
        _ensure_warehouse(db, new_warehouse_id)
        if (
            _request_return_move is None
            and not can_access(user, new_warehouse_id)
        ):
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
                    **(additional_event_metadata or {}),
                    **request_move_metadata,
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
        elif old_status == BoxStatus.returned:
            box.returned_at = None
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
                from_warehouse_id=(
                    _request_return_move.source_warehouse_id
                    if _request_return_move is not None
                    else box.current_warehouse_id
                ),
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
                    **(additional_event_metadata or {}),
                    **request_move_metadata,
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


def move_return_box_for_request(
    db: Session,
    *,
    user: User,
    box: Box,
    request_id: int,
    source_warehouse_id: int,
    target_warehouse_id: int,
    commit: bool = False,
) -> Box:
    """Move a reserved return box without granting public target-ACL bypass."""
    return update_box(
        db,
        user=user,
        box=box,
        new_status=BoxStatus.returned,
        new_warehouse_id=target_warehouse_id,
        note=f"Returned through request #{request_id}",
        commit=commit,
        skip_request_guards=True,
        _request_return_move=_RequestReturnMove(
            request_id=request_id,
            source_warehouse_id=source_warehouse_id,
            target_warehouse_id=target_warehouse_id,
        ),
    )


def reassign_box_lot(
    db: Session,
    *,
    user: User,
    box: Box,
    reason: str,
    expected_version: int,
    lot: str | None = None,
    lot_id: int | None = None,
    target_pallet_id: int | None = None,
    detach_pallet: bool = False,
    commit: bool = True,
) -> Box:
    """Assign one physical box to another lot with an immutable audit trail."""
    if user.role != UserRole.admin:
        raise BoxAccessError("lot reassignment requires admin role")
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise BoxRuleError("a reason is required to reassign a box lot")

    source_lot_id = db.scalar(select(Box.lot_id).where(Box.id == box.id))
    if source_lot_id is None:
        raise BoxRuleError("box not found")
    if db.scalar(
        select(Lot.id).where(
            Lot.id == source_lot_id,
            Lot.merged_into_lot_id.is_(None),
        )
    ) is None:
        raise BoxRuleError("box has no current lot")
    try:
        target_lot = resolve_lot(
            db,
            user=user,
            lot=lot,
            lot_id=lot_id,
            warehouse_id=box.current_warehouse_id,
            lock_for_use=False,
        )
    except LotRuleError as exc:
        raise BoxRuleError(str(exc)) from exc

    # Merge and reassignment take the same deterministic lock order: all Lot
    # identities first (ascending id), then the physical Box row.
    locked_lots = lock_lots_exclusively(db, [source_lot_id, target_lot.id])
    source_lot = locked_lots.get(source_lot_id)
    target_lot = locked_lots.get(target_lot.id)
    if (
        source_lot is None
        or target_lot is None
        or source_lot.merged_into_lot_id is not None
        or target_lot.merged_into_lot_id is not None
    ):
        raise BoxConflictError("source and target lots must both be active")

    preliminary_pallet_id = db.scalar(
        select(Box.pallet_id).where(Box.id == box.id)
    )
    pallet_ids = sorted(
        {
            pallet_id
            for pallet_id in (preliminary_pallet_id, target_pallet_id)
            if pallet_id is not None
        }
    )
    locked_pallets = {
        pallet.id: pallet
        for pallet in db.scalars(
            select(Pallet)
            .where(Pallet.id.in_(pallet_ids))
            .order_by(Pallet.id)
            .with_for_update(of=Pallet)
        ).all()
    }
    locked = db.scalar(
        select(Box).where(Box.id == box.id).with_for_update(of=Box)
    )
    if locked is None:
        raise BoxRuleError("box not found")
    if locked.lot_id != source_lot.id:
        raise BoxConflictError("box lot changed concurrently; refresh and retry")
    if locked.pallet_id != preliminary_pallet_id:
        raise BoxConflictError(
            "box pallet changed concurrently; refresh and retry"
        )
    if locked.archived_at is not None:
        raise BoxConflictError("archived boxes cannot be reassigned")
    if source_lot.version != expected_version:
        raise BoxConflictError(
            f"lot version conflict: expected {expected_version}, current {source_lot.version}"
        )
    if target_lot.id == source_lot.id:
        return locked

    collision = db.scalar(
        select(Box.id).where(
            Box.lot_id == target_lot.id,
            Box.box_number == locked.box_number,
            Box.id != locked.id,
        )
    )
    if collision is not None:
        raise BoxConflictError(
            f"box {locked.box_number!r} already exists in lot {target_lot.name!r}"
        )

    request_ids = _referencing_request_ids(db, locked.id)
    metadata = {
        "operation": "box_lot_reassignment",
        "box_id": locked.id,
        "box_number": locked.box_number,
        "from_lot_id": source_lot.id,
        "from_lot_name": source_lot.name,
        "to_lot_id": target_lot.id,
        "to_lot_name": target_lot.name,
        "request_snapshot_ids_preserved": request_ids,
        "expected_version": expected_version,
    }
    now = datetime.now(UTC)
    try:
        with db.begin_nested():
            target_pallet = None
            if target_pallet_id is not None:
                target_pallet = locked_pallets.get(target_pallet_id)
                if target_pallet is None:
                    raise BoxConflictError("target pallet not found")
            if locked.pallet_id is not None or target_pallet is not None or detach_pallet:
                set_box_pallet_assignment(
                    db,
                    user=user,
                    box=locked,
                    target_pallet=target_pallet,
                    reason=cleaned_reason,
                    expected_lot_id=target_lot.id,
                    metadata={"operation": "lot_reassignment_pallet_change"},
                )
            locked.lot_id = target_lot.id
            locked.lot_record = target_lot
            locked.updated_by_user_id = user.id
            locked.updated_at = now
            # Touching the source lot advances its optimistic version and makes
            # concurrent reassignment/rename attempts fail deterministically.
            source_lot.updated_by_user_id = user.id
            source_lot.version += 1
            db.add(
                LotEvent(
                    lot_id=source_lot.id,
                    event_type=LotEventType.reassigned,
                    old_name=source_lot.name,
                    new_name=target_lot.name,
                    actor_user_id=user.id,
                    reason=cleaned_reason,
                    occurred_at=now,
                    event_metadata=metadata,
                )
            )
            db.add(
                BoxEvent(
                    box_id=locked.id,
                    warehouse_id=locked.current_warehouse_id,
                    event_type=BoxEventType.lot_reassigned,
                    from_status=locked.status,
                    to_status=locked.status,
                    from_warehouse_id=locked.current_warehouse_id,
                    to_warehouse_id=locked.current_warehouse_id,
                    occurred_at=now,
                    user_id=user.id,
                    note=cleaned_reason,
                    event_metadata=metadata,
                )
            )
            db.flush()
    except IntegrityError as exc:
        raise BoxConflictError(
            f"box {locked.box_number!r} already exists in lot {target_lot.name!r}"
        ) from exc
    if commit:
        db.commit()
        db.refresh(locked)
    return locked


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
    affected_warehouse_ids: set[int] = field(default_factory=set)


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
        source_warehouse_id = box.current_warehouse_id
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
        outcome.affected_warehouse_ids.update(
            (source_warehouse_id, updated.current_warehouse_id)
        )

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
    elif box.pallet_id is not None:
        now = datetime.now(UTC)
        archive_reason = (reason or "").strip() or (
            "Archived to preserve pallet assignment history."
        )
        box.archived_at = now
        box.archived_by_user_id = user.id
        box.archive_reason = archive_reason
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
                note=archive_reason,
                event_metadata={
                    "operation": "assigned_box_archive",
                    "pallet_id": box.pallet_id,
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
