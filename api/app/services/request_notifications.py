from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
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
_EMAIL_CLAIM_TIMEOUT = timedelta(minutes=15)
REQUEST_EMAIL_ALLOWED_KINDS = frozenset(
    {
        RequestNotificationKind.staged,
        RequestNotificationKind.submitted,
        RequestNotificationKind.approved,
        RequestNotificationKind.confirmation_received,
    }
)
_REQUEST_EMAIL_ALLOWED_VALUES = frozenset(
    kind.value for kind in REQUEST_EMAIL_ALLOWED_KINDS
)

_DISCARD_KIND_NOT_ALLOWED = "notification_kind_not_allowed"
_DISCARD_RECIPIENT_MISSING = "recipient_user_missing"
_DISCARD_RECIPIENT_INACTIVE = "recipient_user_inactive"
_DISCARD_RECIPIENT_OPTED_OUT = "recipient_email_updates_disabled"
_DISCARD_RECIPIENT_EMAIL_MISSING = "recipient_email_missing"
_TRANSPORT_RESULT_UNKNOWN = "transport_result_unknown"


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
    email = None
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

        if (
            kind not in REQUEST_EMAIL_ALLOWED_KINDS
            or not recipient.email_requests_enabled
            or not recipient.email.strip()
        ):
            continue
        if email is None:
            email = render_request_email(
                request=request,
                warehouse=warehouse,
                kind=kind,
                assignee=assignee,
                detail=detail,
            )
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


def _outbox_eligibility(now: datetime):
    stale_before = now - _EMAIL_CLAIM_TIMEOUT
    return (
        RequestEmailOutbox.sent_at.is_(None),
        RequestEmailOutbox.discarded_at.is_(None),
        RequestEmailOutbox.attempts < _MAX_ATTEMPTS,
        or_(
            RequestEmailOutbox.available_at.is_(None),
            RequestEmailOutbox.available_at <= now,
        ),
        or_(
            RequestEmailOutbox.email_claimed_at.is_(None),
            RequestEmailOutbox.email_claimed_at < stale_before,
        ),
    )


def _outbox_claim_select(now: datetime, exclude_ids: set[int]):
    stmt = (
        select(RequestEmailOutbox.id)
        .where(*_outbox_eligibility(now))
        .order_by(RequestEmailOutbox.created_at, RequestEmailOutbox.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if exclude_ids:
        stmt = stmt.where(RequestEmailOutbox.id.notin_(exclude_ids))
    return stmt


def _claim_next_outbox(
    db: Session, *, now: datetime, exclude_ids: set[int]
) -> tuple[int, datetime] | None:
    """Durably claim one row, with a guarded update for SQLite."""
    row_id = db.scalar(_outbox_claim_select(now, exclude_ids))
    if row_id is None:
        db.commit()
        return None
    claimed = db.execute(
        update(RequestEmailOutbox)
        .where(
            RequestEmailOutbox.id == row_id,
            *_outbox_eligibility(now),
        )
        .values(email_claimed_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    return int(row_id), now


def _release_outbox_claim(
    db: Session, *, row_id: int, claim_token: datetime
) -> None:
    db.execute(
        update(RequestEmailOutbox)
        .where(
            RequestEmailOutbox.id == row_id,
            RequestEmailOutbox.email_claimed_at == claim_token,
        )
        .values(email_claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _discard_claimed_outbox(
    db: Session,
    *,
    row_id: int,
    claim_token: datetime,
    now: datetime,
    reason: str,
) -> None:
    db.execute(
        update(RequestEmailOutbox)
        .where(
            RequestEmailOutbox.id == row_id,
            RequestEmailOutbox.email_claimed_at == claim_token,
            RequestEmailOutbox.sent_at.is_(None),
            RequestEmailOutbox.discarded_at.is_(None),
        )
        .values(
            discarded_at=now,
            discard_reason=reason,
            email_claimed_at=None,
            ok=False,
            error=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _reserve_claimed_attempt(
    db: Session,
    *,
    row_id: int,
    claim_token: datetime,
    now: datetime,
    current_email: str,
) -> int | None:
    """Atomically reserve one bounded transport invocation for this owner."""
    reserved_attempt = db.scalar(
        update(RequestEmailOutbox)
        .where(
            RequestEmailOutbox.id == row_id,
            RequestEmailOutbox.email_claimed_at == claim_token,
            RequestEmailOutbox.sent_at.is_(None),
            RequestEmailOutbox.discarded_at.is_(None),
            RequestEmailOutbox.attempts < _MAX_ATTEMPTS,
        )
        .values(
            recipient_email=current_email,
            attempts=RequestEmailOutbox.attempts + 1,
            last_attempt_at=now,
            ok=False,
            error=_TRANSPORT_RESULT_UNKNOWN,
        )
        .returning(RequestEmailOutbox.attempts)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return int(reserved_attempt) if reserved_attempt is not None else None


def _finalize_claimed_attempt(
    db: Session,
    *,
    row_id: int,
    claim_token: datetime,
    reserved_attempt: int,
    now: datetime,
    ok: bool,
    error: str | None,
) -> bool:
    """Persist a result only while the exact reservation owner still holds."""
    values: dict[str, object | None] = {
        "email_claimed_at": None,
        "ok": ok,
        "error": None if ok else (error or "unknown transport error")[:2000],
    }
    if ok:
        values["sent_at"] = now
    else:
        values["available_at"] = now + timedelta(
            minutes=min(60, 2 ** min(reserved_attempt, 6))
        )
    finalized = db.execute(
        update(RequestEmailOutbox)
        .where(
            RequestEmailOutbox.id == row_id,
            RequestEmailOutbox.email_claimed_at == claim_token,
            RequestEmailOutbox.sent_at.is_(None),
            RequestEmailOutbox.discarded_at.is_(None),
            RequestEmailOutbox.attempts == reserved_attempt,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return finalized.rowcount == 1


def _dispatch_claimed_outbox(
    db: Session, *, row_id: int, claim_token: datetime, now: datetime
) -> str:
    row = db.get(RequestEmailOutbox, row_id)
    if row is None:
        db.commit()
        return "skipped"

    # Revalidate all mutable policy inputs after the durable claim and
    # immediately before transport. Sent history is never rewritten.
    if row.sent_at is not None:
        _release_outbox_claim(db, row_id=row_id, claim_token=claim_token)
        return "skipped"
    if row.discarded_at is not None:
        _release_outbox_claim(db, row_id=row_id, claim_token=claim_token)
        return "skipped"
    if row.kind not in _REQUEST_EMAIL_ALLOWED_VALUES:
        _discard_claimed_outbox(
            db,
            row_id=row_id,
            claim_token=claim_token,
            now=now,
            reason=_DISCARD_KIND_NOT_ALLOWED,
        )
        return "skipped"

    recipient = (
        db.get(User, row.recipient_user_id)
        if row.recipient_user_id is not None
        else None
    )
    if recipient is None:
        _discard_claimed_outbox(
            db,
            row_id=row_id,
            claim_token=claim_token,
            now=now,
            reason=_DISCARD_RECIPIENT_MISSING,
        )
        return "skipped"
    if not recipient.is_active:
        _discard_claimed_outbox(
            db,
            row_id=row_id,
            claim_token=claim_token,
            now=now,
            reason=_DISCARD_RECIPIENT_INACTIVE,
        )
        return "skipped"
    if not recipient.email_requests_enabled:
        _discard_claimed_outbox(
            db,
            row_id=row_id,
            claim_token=claim_token,
            now=now,
            reason=_DISCARD_RECIPIENT_OPTED_OUT,
        )
        return "skipped"
    current_email = recipient.email.strip()
    if not current_email:
        _discard_claimed_outbox(
            db,
            row_id=row_id,
            claim_token=claim_token,
            now=now,
            reason=_DISCARD_RECIPIENT_EMAIL_MISSING,
        )
        return "skipped"

    # Reserve the bounded attempt before calling the external transport. If
    # the process dies after this commit, stale-claim recovery can only reserve
    # another attempt while this durable count remains below the cap.
    subject = row.subject
    html_body = row.html_body
    text_body = row.text_body
    reserved_attempt = _reserve_claimed_attempt(
        db,
        row_id=row_id,
        claim_token=claim_token,
        now=now,
        current_email=current_email,
    )
    if reserved_attempt is None:
        return "skipped"

    try:
        ok, error = send_email(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            to=[current_email],
        )
    except Exception as exc:  # pragma: no cover - transport normally normalizes errors
        ok, error = False, str(exc)
    finalized = _finalize_claimed_attempt(
        db,
        row_id=row_id,
        claim_token=claim_token,
        reserved_attempt=reserved_attempt,
        now=now,
        ok=ok,
        error=error,
    )
    if not finalized:
        return "skipped"
    return "sent" if ok else "failed"


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
    """Claim and attempt up to 100 due rows; successful rows send once."""
    current = now or datetime.now(UTC)
    outcome = RequestDispatchOutcome()
    processed: set[int] = set()
    while len(processed) < 100:
        claim = _claim_next_outbox(
            db,
            now=current,
            exclude_ids=processed,
        )
        if claim is None:
            break
        row_id, claim_token = claim
        processed.add(row_id)
        result = _dispatch_claimed_outbox(
            db,
            row_id=row_id,
            claim_token=claim_token,
            now=current,
        )
        if result == "sent":
            outcome.sent += 1
        elif result == "failed":
            outcome.failed += 1
        else:
            outcome.skipped += 1
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
    "REQUEST_EMAIL_ALLOWED_KINDS",
    "RequestDispatchOutcome",
    "dispatch_request_email_outbox",
    "dispatch_request_email_safe",
    "enqueue_request_event",
    "mark_overdue_requests",
]
