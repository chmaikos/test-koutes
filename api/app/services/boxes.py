"""Domain service for box mutations.

Centralises status-transition validation, timestamp bookkeeping and audit-log
writes so routers stay thin and we never forget to write a `box_events` row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.boxes import Box, BoxEvent, BoxEventType, BoxStatus
from app.models.users import User
from app.models.warehouses import Warehouse
from app.services.acl import can_access

# Valid forward transitions; "returned" is terminal.
_ALLOWED_TRANSITIONS: dict[BoxStatus, set[BoxStatus]] = {
    BoxStatus.received: {BoxStatus.ready_to_return},
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
    """A unique-constraint violation (duplicate box_number)."""


class BoxAccessError(BoxRuleError):
    """The caller does not have ACL access to the warehouse involved."""


def _ensure_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    wh = db.get(Warehouse, warehouse_id)
    if wh is None:
        raise BoxRuleError(f"warehouse {warehouse_id} does not exist")
    return wh


def create_box(
    db: Session,
    *,
    user: User,
    box_number: str,
    owner: str,
    warehouse_id: int,
    note: str | None = None,
) -> Box:
    _ensure_warehouse(db, warehouse_id)
    if not can_access(user, warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {warehouse_id}"
        )
    cleaned_number = box_number.strip()
    if not cleaned_number:
        raise BoxRuleError("box_number is required")
    from sqlalchemy import select

    existing = db.scalar(select(Box).where(Box.box_number == cleaned_number))
    if existing is not None:
        raise BoxConflictError(f"box {cleaned_number!r} already exists")
    now = datetime.now(UTC)
    box = Box(
        box_number=cleaned_number,
        owner=owner.strip(),
        current_warehouse_id=warehouse_id,
        status=BoxStatus.received,
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
            to_status=BoxStatus.received,
            from_warehouse_id=None,
            to_warehouse_id=warehouse_id,
            occurred_at=now,
            user_id=user.id,
            note=note,
        )
    )
    db.commit()
    db.refresh(box)
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
    new_owner: str | None = None,
    note: str | None = None,
    force: bool = False,
) -> Box:
    now = datetime.now(UTC)
    events: list[BoxEvent] = []
    audit_note = _audit_note(note, force=force)

    # ACL: the caller must have access to the box's *current* warehouse to
    # touch it at all, and to the *target* warehouse if they're moving it.
    # Admins bypass via can_access.
    if not can_access(user, box.current_warehouse_id):
        raise BoxAccessError(
            f"no access to warehouse {box.current_warehouse_id}"
        )

    if new_owner is not None and new_owner != box.owner:
        box.owner = new_owner.strip()

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
            )
        )

    if not events and new_owner is None:
        return box

    box.updated_by_user_id = user.id
    box.updated_at = now
    for ev in events:
        db.add(ev)
    db.commit()
    db.refresh(box)
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
    affected_warehouse_ids: set[int] = field(default_factory=set)
    skipped: list[BulkSkipEntry] = field(default_factory=list)


def delete_box(db: Session, *, box: Box) -> int:
    """Hard-delete a box. Returns its current warehouse id so the caller can
    publish a single SSE update afterwards. Event rows cascade out via the
    `BoxEvent.box_id` FK (`ondelete="CASCADE"`)."""
    warehouse_id = box.current_warehouse_id
    db.delete(box)
    db.commit()
    return warehouse_id


def bulk_delete_boxes(
    db: Session, *, box_ids: list[int]
) -> BulkDeleteOutcome:
    """Hard-delete many boxes, reporting missing ids as skipped."""
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
        outcome.affected_warehouse_ids.add(box.current_warehouse_id)
        outcome.deleted_ids.append(box.id)
        db.delete(box)

    if outcome.deleted_ids:
        db.commit()
    return outcome
