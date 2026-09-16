from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.models.notifications import (
    InAppNotification,
    RequestEmailOutbox,
    RequestNotificationKind,
)
from app.models.requests import (
    BoxRequest,
    BoxRequestDirection,
    BoxRequestStatus,
)
from app.models.users import User, UserRole
from app.services import request_notifications as notifications
from app.services.request_notifications import (
    REQUEST_EMAIL_ALLOWED_KINDS,
    dispatch_request_email_outbox,
    enqueue_request_event,
)

_EXPECTED_EMAIL_KINDS = {
    RequestNotificationKind.staged,
    RequestNotificationKind.submitted,
    RequestNotificationKind.approved,
    RequestNotificationKind.confirmation_received,
}


def _request(session: Session, requester: User | None = None) -> BoxRequest:
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
        requester_user_id=requester.id if requester is not None else None,
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def _outbox(
    session: Session,
    *,
    request: BoxRequest,
    recipient: User | None,
    kind: str = RequestNotificationKind.approved.value,
    attempts: int = 0,
    available_at: datetime | None = None,
) -> RequestEmailOutbox:
    row = RequestEmailOutbox(
        request_id=request.id,
        recipient_user_id=recipient.id if recipient is not None else None,
        recipient_email="stale@example.com",
        kind=kind,
        subject="Request update",
        html_body="<p>Request update</p>",
        text_body="Request update",
        idempotency_key=f"test-outbox:{request.id}:{kind}:{datetime.now(UTC).timestamp()}",
        attempts=attempts,
        available_at=available_at or datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.mark.parametrize("kind", list(RequestNotificationKind))
def test_every_kind_preserves_in_app_but_only_allowlist_enqueues_email(
    session: Session,
    make_user,
    kind: RequestNotificationKind,
) -> None:
    assert REQUEST_EMAIL_ALLOWED_KINDS == _EXPECTED_EMAIL_KINDS
    recipient = make_user(UserRole.admin)
    request = _request(session, recipient)

    first = enqueue_request_event(
        session,
        request=request,
        kind=kind,
        event_token=f"all-kinds:{kind.value}",
    )
    session.commit()
    second = enqueue_request_event(
        session,
        request=request,
        kind=kind,
        event_token=f"all-kinds:{kind.value}",
    )
    session.commit()

    in_app = list(
        session.scalars(
            select(InAppNotification).where(
                InAppNotification.request_id == request.id,
                InAppNotification.kind == kind.value,
            )
        )
    )
    outbox = list(
        session.scalars(
            select(RequestEmailOutbox).where(
                RequestEmailOutbox.request_id == request.id
            )
        )
    )
    assert len(first) == 1
    assert second == []
    assert len(in_app) == 1
    assert {row.user_id for row in in_app} == {recipient.id}
    assert len(outbox) == (1 if kind in _EXPECTED_EMAIL_KINDS else 0)
    if outbox:
        assert outbox[0].kind == kind.value


def test_request_email_preference_does_not_disable_in_app(
    session: Session, make_user
) -> None:
    recipient = make_user(UserRole.admin)
    recipient.email_requests_enabled = False
    session.commit()
    request = _request(session, recipient)

    enqueue_request_event(
        session,
        request=request,
        kind=RequestNotificationKind.approved,
        event_token="preference",
    )
    session.commit()

    assert session.scalar(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.request_id == request.id
        )
    ) == 1
    assert session.scalar(
        select(func.count(RequestEmailOutbox.id)).where(
            RequestEmailOutbox.request_id == request.id
        )
    ) == 0


def test_auto_completed_xlsx_receipt_batch_has_no_notification_event(
    client, session: Session
) -> None:
    response = client.post(
        "/api/boxes/import-mapped",
        json={
            "warehouse_id": 1,
            "items": [
                {
                    "box_number": "101",
                    "lot": "AUTO-RECEIPT",
                    "pallet_number": "AUTO-PALLET",
                },
                {
                    "box_number": "102",
                    "lot": "AUTO-RECEIPT",
                    "pallet_number": "AUTO-PALLET",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    request_id = response.json()["receipt_request_ids"][0]
    assert session.scalar(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.request_id == request_id
        )
    ) == 0
    assert session.scalar(
        select(func.count(RequestEmailOutbox.id)).where(
            RequestEmailOutbox.request_id == request_id
        )
    ) == 0


def test_dispatch_discards_invalid_kind_without_transport_or_attempt(
    session: Session, make_user, monkeypatch
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    row = _outbox(
        session,
        request=request,
        recipient=recipient,
        kind=RequestNotificationKind.rejected.value,
        attempts=2,
    )
    row.ok = True
    row.error = "old failure"
    session.commit()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **kwargs: (calls.append(kwargs) or True, None),
    )

    result = dispatch_request_email_outbox(session)

    session.refresh(row)
    assert result.skipped == 1
    assert calls == []
    assert row.attempts == 2
    assert row.discard_reason == "notification_kind_not_allowed"
    assert row.discarded_at is not None
    assert row.ok is False
    assert row.error is None


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("inactive", "recipient_user_inactive"),
        ("opted_out", "recipient_email_updates_disabled"),
        ("blank_email", "recipient_email_missing"),
    ],
)
def test_dispatch_revalidates_mutable_recipient_state(
    session: Session,
    make_user,
    monkeypatch,
    change: str,
    reason: str,
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    row = _outbox(session, request=request, recipient=recipient)
    if change == "inactive":
        recipient.is_active = False
    elif change == "opted_out":
        recipient.email_requests_enabled = False
    else:
        recipient.email = "   "
    session.commit()
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **kwargs: (calls.append(kwargs) or True, None),
    )

    result = dispatch_request_email_outbox(session)

    session.refresh(row)
    assert result.skipped == 1
    assert calls == []
    assert row.attempts == 0
    assert row.discard_reason == reason
    assert row.discarded_at is not None
    assert row.email_claimed_at is None


@pytest.mark.parametrize("delete_recipient", [False, True])
def test_dispatch_discards_null_or_deleted_recipient(
    session: Session,
    make_user,
    monkeypatch,
    delete_recipient: bool,
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    row = _outbox(
        session,
        request=request,
        recipient=recipient if delete_recipient else None,
    )
    if delete_recipient:
        session.delete(recipient)
        session.commit()
    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **_: pytest.fail("transport must not be called"),
    )

    dispatch_request_email_outbox(session)

    session.refresh(row)
    assert row.recipient_user_id is None
    assert row.attempts == 0
    assert row.discard_reason == "recipient_user_missing"
    assert row.discarded_at is not None


def test_dispatch_uses_and_records_current_recipient_email(
    session: Session, make_user, monkeypatch
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    row = _outbox(session, request=request, recipient=recipient)
    recipient.email = " current@example.com "
    session.commit()
    calls: list[dict[str, object]] = []

    def _send(**kwargs):
        calls.append(kwargs)
        return True, None

    monkeypatch.setattr(notifications, "send_email", _send)
    first = dispatch_request_email_outbox(session)
    second = dispatch_request_email_outbox(session)

    session.refresh(row)
    assert first.sent == 1
    assert second.sent == 0
    assert [call["to"] for call in calls] == [["current@example.com"]]
    assert row.recipient_email == "current@example.com"
    assert row.attempts == 1
    assert row.sent_at is not None
    assert row.email_claimed_at is None


def test_discarded_and_max_attempt_rows_are_inert(
    session: Session, make_user, monkeypatch
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    discarded = _outbox(session, request=request, recipient=recipient)
    discarded.discarded_at = datetime.now(UTC)
    discarded.discard_reason = "notification_policy_reduced"
    exhausted = _outbox(
        session,
        request=request,
        recipient=recipient,
        attempts=notifications._MAX_ATTEMPTS,
    )
    sent = _outbox(
        session,
        request=request,
        recipient=recipient,
        kind=RequestNotificationKind.rejected.value,
    )
    sent.sent_at = datetime.now(UTC)
    sent.ok = True
    session.commit()
    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **_: pytest.fail("transport must not be called"),
    )

    result = dispatch_request_email_outbox(session)

    assert result == notifications.RequestDispatchOutcome()
    session.refresh(discarded)
    session.refresh(exhausted)
    session.refresh(sent)
    assert discarded.attempts == 0
    assert exhausted.attempts == notifications._MAX_ATTEMPTS
    assert sent.sent_at is not None
    assert sent.ok is True
    assert sent.discarded_at is None


def test_transport_failure_retries_with_backoff_then_sends_once(
    session: Session, make_user, monkeypatch
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    start = datetime.now(UTC)
    row = _outbox(
        session,
        request=request,
        recipient=recipient,
        available_at=start,
    )
    outcomes = iter([(False, "smtp down"), (True, None)])
    calls: list[str] = []
    reservations: list[tuple[int, str | None, bool]] = []

    def _send(**_):
        calls.append("attempt")
        SessionOther = sessionmaker(bind=session.get_bind(), future=True)
        with SessionOther() as observer:
            reserved = observer.get(RequestEmailOutbox, row.id)
            assert reserved is not None
            reservations.append(
                (
                    reserved.attempts,
                    reserved.error,
                    reserved.email_claimed_at is not None,
                )
            )
        return next(outcomes)

    monkeypatch.setattr(notifications, "send_email", _send)
    failed = dispatch_request_email_outbox(session, now=start)
    too_soon = dispatch_request_email_outbox(
        session, now=start + timedelta(minutes=1)
    )
    sent = dispatch_request_email_outbox(
        session, now=start + timedelta(minutes=3)
    )
    duplicate = dispatch_request_email_outbox(
        session, now=start + timedelta(minutes=4)
    )

    session.refresh(row)
    assert failed.failed == 1
    assert too_soon == notifications.RequestDispatchOutcome()
    assert sent.sent == 1
    assert duplicate == notifications.RequestDispatchOutcome()
    assert calls == ["attempt", "attempt"]
    assert reservations == [
        (1, "transport_result_unknown", True),
        (2, "transport_result_unknown", True),
    ]
    assert row.attempts == 2
    assert row.sent_at is not None
    assert row.sent_at.replace(tzinfo=UTC) == start + timedelta(minutes=3)
    assert row.error is None
    assert row.ok is True


def test_crashed_reserved_attempt_is_durable_and_stale_claim_retries(
    session: Session, make_user, monkeypatch
) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    recipient = make_user(UserRole.admin)
    request = _request(session)
    start = datetime.now(UTC)
    row = _outbox(
        session,
        request=request,
        recipient=recipient,
        available_at=start,
    )

    def _crash(**_):
        raise SimulatedProcessCrash

    monkeypatch.setattr(notifications, "send_email", _crash)
    with pytest.raises(SimulatedProcessCrash):
        dispatch_request_email_outbox(session, now=start)

    session.refresh(row)
    assert row.attempts == 1
    assert row.last_attempt_at is not None
    assert row.last_attempt_at.replace(tzinfo=UTC) == start
    assert row.email_claimed_at is not None
    assert row.error == "transport_result_unknown"
    assert row.ok is False
    assert row.sent_at is None

    calls: list[str] = []
    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **_: (calls.append("retry") or True, None),
    )
    recovered = dispatch_request_email_outbox(
        session,
        now=start + notifications._EMAIL_CLAIM_TIMEOUT + timedelta(seconds=1),
    )

    session.refresh(row)
    assert recovered.sent == 1
    assert calls == ["retry"]
    assert row.attempts == 2
    assert row.email_claimed_at is None
    assert row.error is None
    assert row.sent_at is not None


def test_fifth_reserved_attempt_crash_is_permanently_exhausted(
    session: Session, make_user, monkeypatch
) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    recipient = make_user(UserRole.admin)
    request = _request(session)
    start = datetime.now(UTC)
    row = _outbox(
        session,
        request=request,
        recipient=recipient,
        attempts=notifications._MAX_ATTEMPTS - 1,
        available_at=start,
    )
    calls: list[str] = []

    def _crash(**_):
        calls.append("fifth")
        raise SimulatedProcessCrash

    monkeypatch.setattr(notifications, "send_email", _crash)
    with pytest.raises(SimulatedProcessCrash):
        dispatch_request_email_outbox(session, now=start)

    session.refresh(row)
    assert row.attempts == notifications._MAX_ATTEMPTS
    assert row.error == "transport_result_unknown"
    assert row.email_claimed_at is not None

    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **_: calls.append("unexpected"),
    )
    after_stale = dispatch_request_email_outbox(
        session,
        now=start + notifications._EMAIL_CLAIM_TIMEOUT + timedelta(hours=1),
    )

    session.refresh(row)
    assert after_stale == notifications.RequestDispatchOutcome()
    assert calls == ["fifth"]
    assert row.attempts == notifications._MAX_ATTEMPTS
    assert row.sent_at is None


def test_fresh_claim_blocks_other_dispatcher_and_stale_claim_recovers(
    session: Session, make_user, monkeypatch
) -> None:
    recipient = make_user(UserRole.admin)
    request = _request(session)
    row = _outbox(session, request=request, recipient=recipient)
    now = datetime.now(UTC)
    SessionOther = sessionmaker(bind=session.get_bind(), future=True)
    first = SessionOther()
    second = SessionOther()
    try:
        assert notifications._claim_next_outbox(
            first, now=now, exclude_ids=set()
        ) == (row.id, now)
        assert notifications._claim_next_outbox(
            second, now=now, exclude_ids=set()
        ) is None
    finally:
        first.close()
        second.close()

    row.email_claimed_at = now - notifications._EMAIL_CLAIM_TIMEOUT - timedelta(
        seconds=1
    )
    session.commit()
    calls: list[str] = []
    monkeypatch.setattr(
        notifications,
        "send_email",
        lambda **_: (calls.append("attempt") or True, None),
    )

    result = dispatch_request_email_outbox(session, now=now)

    session.refresh(row)
    assert result.sent == 1
    assert calls == ["attempt"]
    assert row.email_claimed_at is None


def test_stale_owner_cannot_reserve_finalize_or_clobber_takeover_near_cap(
    session: Session, make_user, monkeypatch
) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    recipient = make_user(UserRole.admin)
    request = _request(session)
    start = datetime.now(UTC)
    takeover_at = start + notifications._EMAIL_CLAIM_TIMEOUT + timedelta(
        seconds=1
    )
    row = _outbox(
        session,
        request=request,
        recipient=recipient,
        attempts=notifications._MAX_ATTEMPTS - 1,
        available_at=start,
    )
    SessionOther = sessionmaker(bind=session.get_bind(), future=True)
    stale_worker = SessionOther()
    replacement_worker = SessionOther()
    calls: list[str] = []
    try:
        stale_claim = notifications._claim_next_outbox(
            stale_worker,
            now=start,
            exclude_ids=set(),
        )
        assert stale_claim == (row.id, start)
        replacement_claim = notifications._claim_next_outbox(
            replacement_worker,
            now=takeover_at,
            exclude_ids=set(),
        )
        assert replacement_claim == (row.id, takeover_at)

        monkeypatch.setattr(
            notifications,
            "send_email",
            lambda **_: calls.append("unexpected stale call"),
        )
        assert notifications._dispatch_claimed_outbox(
            stale_worker,
            row_id=row.id,
            claim_token=start,
            now=takeover_at,
        ) == "skipped"
        assert notifications._finalize_claimed_attempt(
            stale_worker,
            row_id=row.id,
            claim_token=start,
            reserved_attempt=notifications._MAX_ATTEMPTS - 1,
            now=takeover_at,
            ok=True,
            error=None,
        ) is False

        session.refresh(row)
        assert row.attempts == notifications._MAX_ATTEMPTS - 1
        assert row.email_claimed_at is not None
        assert row.email_claimed_at.replace(tzinfo=UTC) == takeover_at
        assert calls == []

        def _crash_on_fifth(**_):
            calls.append("replacement fifth call")
            raise SimulatedProcessCrash

        monkeypatch.setattr(notifications, "send_email", _crash_on_fifth)
        with pytest.raises(SimulatedProcessCrash):
            notifications._dispatch_claimed_outbox(
                replacement_worker,
                row_id=row.id,
                claim_token=takeover_at,
                now=takeover_at,
            )

        session.refresh(row)
        assert row.attempts == notifications._MAX_ATTEMPTS
        assert row.email_claimed_at is not None
        assert row.email_claimed_at.replace(tzinfo=UTC) == takeover_at

        monkeypatch.setattr(
            notifications,
            "send_email",
            lambda **_: calls.append("sixth call"),
        )
        assert notifications._dispatch_claimed_outbox(
            stale_worker,
            row_id=row.id,
            claim_token=start,
            now=takeover_at + timedelta(hours=1),
        ) == "skipped"
        assert notifications._dispatch_claimed_outbox(
            replacement_worker,
            row_id=row.id,
            claim_token=takeover_at,
            now=takeover_at + timedelta(hours=1),
        ) == "skipped"
    finally:
        stale_worker.close()
        replacement_worker.close()

    session.refresh(row)
    assert calls == ["replacement fifth call"]
    assert row.attempts == notifications._MAX_ATTEMPTS
    assert row.email_claimed_at is not None
    assert row.email_claimed_at.replace(tzinfo=UTC) == takeover_at
    assert row.sent_at is None


def test_postgresql_outbox_claim_uses_skip_locked() -> None:
    stmt = notifications._outbox_claim_select(datetime.now(UTC), set())
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE SKIP LOCKED" in sql
