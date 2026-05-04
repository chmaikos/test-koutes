"""Domain service for box mutations.

Centralises status-transition validation, timestamp bookkeeping and audit-log
writes so routers stay thin and we never forget to write a `box_events` row.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.boxes import Box, BoxEvent, BoxEventType, BoxStatus
from app.models.users import User
from app.models.warehouses import Warehouse

# Valid forward transitions; "returned" is terminal.
_ALLOWED_TRANSITIONS: dict[BoxStatus, set[BoxStatus]] = {
    BoxStatus.received: {BoxStatus.in_progress, BoxStatus.ready_to_return},
    BoxStatus.in_progress: {BoxStatus.processing_complete, BoxStatus.ready_to_return},
    BoxStatus.processing_complete: {BoxStatus.ready_to_return},
    BoxStatus.ready_to_return: {BoxStatus.returned},
    BoxStatus.returned: set(),
}


def _ensure_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    wh = db.get(Warehouse, warehouse_id)
    if wh is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"warehouse {warehouse_id} does not exist",
        )
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
    existing = db.scalar(select(Box).where(Box.box_number == box_number))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"box {box_number!r} already exists",
        )
    now = datetime.now(UTC)
    box = Box(
        box_number=box_number.strip(),
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


def update_box(
    db: Session,
    *,
    user: User,
    box: Box,
    new_status: BoxStatus | None = None,
    new_warehouse_id: int | None = None,
    new_owner: str | None = None,
    note: str | None = None,
) -> Box:
    now = datetime.now(UTC)
    events: list[BoxEvent] = []

    if new_owner is not None and new_owner != box.owner:
        box.owner = new_owner.strip()

    if new_warehouse_id is not None and new_warehouse_id != box.current_warehouse_id:
        _ensure_warehouse(db, new_warehouse_id)
        if box.status == BoxStatus.returned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot move a returned box",
            )
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
                note=note,
            )
        )
        box.current_warehouse_id = new_warehouse_id

    if new_status is not None and new_status != box.status:
        allowed = _ALLOWED_TRANSITIONS.get(box.status, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"cannot transition from {box.status.value} to {new_status.value}",
            )
        old_status = box.status
        box.status = new_status
        if new_status == BoxStatus.processing_complete:
            box.processing_completed_at = now
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
                note=note,
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
