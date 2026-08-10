from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models.notifications import InAppNotification
from app.schemas.notifications import (
    MarkNotificationsRead,
    NotificationOut,
    NotificationPage,
    UnreadCountOut,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
def list_notifications(
    db: DbSession,
    user: CurrentUser,
    unread_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> NotificationPage:
    stmt = select(InAppNotification).where(InAppNotification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(InAppNotification.read_at.is_(None))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    unread = int(
        db.scalar(
            select(func.count(InAppNotification.id)).where(
                InAppNotification.user_id == user.id,
                InAppNotification.read_at.is_(None),
            )
        )
        or 0
    )
    rows = db.scalars(
        stmt.order_by(
            InAppNotification.created_at.desc(), InAppNotification.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return NotificationPage(
        items=[NotificationOut.model_validate(row) for row in rows],
        total=total,
        unread=unread,
        page=page,
        page_size=page_size,
    )


@router.get("/unread", response_model=UnreadCountOut)
def unread_count(db: DbSession, user: CurrentUser) -> UnreadCountOut:
    unread = int(
        db.scalar(
            select(func.count(InAppNotification.id)).where(
                InAppNotification.user_id == user.id,
                InAppNotification.read_at.is_(None),
            )
        )
        or 0
    )
    return UnreadCountOut(unread=unread)


@router.post("/mark-read", response_model=UnreadCountOut)
def mark_read(
    payload: MarkNotificationsRead,
    db: DbSession,
    user: CurrentUser,
) -> UnreadCountOut:
    stmt = select(InAppNotification).where(
        InAppNotification.user_id == user.id,
        InAppNotification.read_at.is_(None),
    )
    if payload.notification_ids is not None:
        ids = set(payload.notification_ids)
        if not ids:
            return unread_count(db, user)
        stmt = stmt.where(InAppNotification.id.in_(ids))
    now = datetime.now(UTC)
    for row in db.scalars(stmt).all():
        row.read_at = now
    db.commit()
    return unread_count(db, user)


__all__ = ["router"]
