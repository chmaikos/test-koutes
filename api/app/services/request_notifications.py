from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.notifications import (
    InAppNotification,
    RequestEmailOutbox,
    RequestNotificationKind,
)
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestEvent,
    BoxRequestEventType,
)
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.acl import can_access
from app.services.graph_email import send_email
from app.services.request_email import (
    notification_copy,
    render_request_email,
)

logger = logging.getLogger("warehouse.request_notifications")
_MAX_ATTEMPTS = 5


@dataclass
class RequestDispatchOutcome:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


def _request_recipients(
    db: Session,
    request: BoxRequest,
    kind: RequestNotificationKind,
) -> list[User]:
    if (
        request.origin.value in ("xlsx_import", "manual_entry")
        and kind
        in (
            RequestNotificationKind.approved,
            RequestNotificationKind.rejected,
            RequestNotificationKind.quarantine_released,
            RequestNotificationKind.quarantine_rejected,
        )
    ):
        candidates = list(
            db.scalars(
                select(User).where(
                    User.is_active.is_(True),
                    User.role == UserRole.admin,
                )
            ).all()
        )
        recipients = [
            candidate
            for candidate in candidates
            if can_access(candidate, request.warehouse_id)
        ]
        if request.requester_user_id is not None:
            requester = db.get(User, request.requester_user_id)
            if requester is not None and requester.is_active and requester not in recipients:
                recipients.append(requester)
        return recipients
    if kind in (
        RequestNotificationKind.submitted,
        RequestNotificationKind.staged,
        RequestNotificationKind.document_needed,
    ):
        candidates = db.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.role.in_(
                    (
                        UserRole.admin,
                        UserRole.warehouse_mover,
                    )
                ),
            )
        ).all()
        recipients = [
            user
            for user in candidates
            if (
                kind == RequestNotificationKind.submitted
                and user.role in (UserRole.admin, UserRole.warehouse_mover)
            )
            or (
                kind in (
                    RequestNotificationKind.staged,
                    RequestNotificationKind.document_needed,
                )
                and user.role == UserRole.admin
            )
            if can_access(user, request.warehouse_id)
        ]
        if request.requester_user_id is not None:
            requester = db.get(User, request.requester_user_id)
            if requester is not None and requester.is_active and requester not in recipients:
                recipients.append(requester)
        return recipients
    if kind == RequestNotificationKind.overdue:
        candidates = db.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.role.in_((UserRole.admin, UserRole.warehouse_mover)),
            )
        ).all()
        return [user for user in candidates if can_access(user, request.warehouse_id)]

    ids = {
        user_id
        for user_id in (request.requester_user_id, request.assigned_mover_user_id)
        if user_id is not None
    }
    if not ids:
        return []
    return list(
        db.scalars(
            select(User).where(User.id.in_(ids), User.is_active.is_(True))
        ).all()
    )


def enqueue_request_event(
    db: Session,
    *,
    request: BoxRequest,
    kind: RequestNotificationKind,
    event_token: str,
    detail: str | None = None,
    exclude_user_id: int | None = None,
) -> list[InAppNotification]:
    """Persist in-app and email work atomically with a request mutation."""
    warehouse = db.get(Warehouse, request.warehouse_id)
    if warehouse is None:
        logger.warning("request %s references missing warehouse", request.id)
        return []
    assignee = (
        db.get(User, request.assigned_mover_user_id)
        if request.assigned_mover_user_id is not None
        else None
    )
    title, body = notification_copy(
        request, warehouse, kind, detail=detail
    )
    email = render_request_email(
        request=request,
        warehouse=warehouse,
        kind=kind,
        assignee=assignee,
        detail=detail,
    )
    created: list[InAppNotification] = []
    for recipient in _request_recipients(db, request, kind):
        if recipient.id == exclude_user_id:
            continue
        base_key = f"request:{request.id}:{event_token}:{recipient.id}"
        notification_key = f"in-app:{base_key}"
        existing = db.scalar(
            select(InAppNotification.id).where(
                InAppNotification.idempotency_key == notification_key
            )
        )
        if existing is None:
            notification = InAppNotification(
                user_id=recipient.id,
                request_id=request.id,
                warehouse_id=request.warehouse_id,
                kind=kind.value,
                title=title,
                body=body,
                deep_link=f"/requests/{request.id}",
                idempotency_key=notification_key,
            )
            db.add(notification)
            created.append(notification)

        if not recipient.email_requests_enabled or not recipient.email.strip():
            continue
        outbox_key = f"email:{base_key}"
        queued = db.scalar(
            select(RequestEmailOutbox.id).where(
                RequestEmailOutbox.idempotency_key == outbox_key
            )
        )
        if queued is None:
            db.add(
                RequestEmailOutbox(
                    request_id=request.id,
                    recipient_user_id=recipient.id,
                    recipient_email=recipient.email.strip(),
                    kind=kind.value,
                    subject=email.subject,
                    html_body=email.html,
                    text_body=email.text,
                    idempotency_key=outbox_key,
                )
            )
    return created


def mark_overdue_requests(db: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    requests = db.scalars(
        select(BoxRequest).where(
            BoxRequest.status.in_(ACTIVE_REQUEST_STATUSES),
            BoxRequest.sla_deadline.is_not(None),
            BoxRequest.sla_deadline < current,
        )
    ).all()
    created = 0
    for request in requests:
        token = f"overdue:{request.id}"
        already_recorded = db.scalar(
            select(BoxRequestEvent.id).where(
                BoxRequestEvent.request_id == request.id,
                BoxRequestEvent.event_type == BoxRequestEventType.overdue,
            )
        )
        if already_recorded is not None:
            continue
        db.add(
            BoxRequestEvent(
                request_id=request.id,
                event_type=BoxRequestEventType.overdue,
                from_status=request.status,
                to_status=request.status,
                note=f"SLA deadline passed at {request.sla_deadline.isoformat()}.",
                occurred_at=current,
            )
        )
        enqueue_request_event(
            db,
            request=request,
            kind=RequestNotificationKind.overdue,
            event_token=token,
        )
        created += 1
    if created:
        db.commit()
    return created


def dispatch_request_email_outbox(
    db: Session, *, now: datetime | None = None
) -> RequestDispatchOutcome:
    """Attempt due outbox rows; successful rows are never sent again."""
    current = now or datetime.now(UTC)
    rows = db.scalars(
        select(RequestEmailOutbox)
        .where(
            RequestEmailOutbox.sent_at.is_(None),
            RequestEmailOutbox.attempts < _MAX_ATTEMPTS,
            or_(
                RequestEmailOutbox.available_at.is_(None),
                RequestEmailOutbox.available_at <= current,
            ),
        )
        .order_by(RequestEmailOutbox.created_at, RequestEmailOutbox.id)
        .limit(100)
    ).all()
    outcome = RequestDispatchOutcome()
    for row in rows:
        ok, error = send_email(
            subject=row.subject,
            html_body=row.html_body,
            text_body=row.text_body,
            to=[row.recipient_email],
        )
        row.attempts += 1
        row.last_attempt_at = current
        row.ok = ok
        row.error = None if ok else (error or "unknown transport error")[:2000]
        if ok:
            row.sent_at = current
            outcome.sent += 1
        else:
            row.available_at = current + timedelta(
                minutes=min(60, 2 ** min(row.attempts, 6))
            )
            outcome.failed += 1
        db.commit()
    return outcome


def dispatch_request_email_safe(db: Session) -> RequestDispatchOutcome:
    try:
        mark_overdue_requests(db)
        return dispatch_request_email_outbox(db)
    except Exception:  # pragma: no cover - scheduler safety net
        db.rollback()
        logger.exception("request email dispatch failed")
        return RequestDispatchOutcome()


__all__ = [
    "RequestDispatchOutcome",
    "dispatch_request_email_outbox",
    "dispatch_request_email_safe",
    "enqueue_request_event",
    "mark_overdue_requests",
]
