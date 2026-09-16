"""Opening-email dispatcher policy tests.

We monkeypatch :func:`app.services.alerts.send_alert_email` to avoid any
real Graph traffic and to capture the exact (subject, recipients) tuples
the dispatcher hands to it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.config import get_settings
from app.models.alerts import (
    Alert,
    AlertNotification,
    AlertNotificationKind,
    AlertType,
)
from app.models.users import UserRole
from app.services import alerts as alerts_service
from app.services.alerts import dispatch_pending_notifications


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def fake_graph(monkeypatch):
    """Capture send_alert_email calls and let each test rule on success."""
    calls: list[dict] = []
    outcome = {"ok": True, "error": None}

    def _send(*, subject: str, html_body: str, to: list[str] | None = None):
        calls.append(
            {"subject": subject, "to": list(to or []), "html": html_body}
        )
        return outcome["ok"], outcome["error"]

    monkeypatch.setattr(alerts_service, "send_alert_email", _send)

    class Handle:
        def __init__(self):
            self.calls = calls

        def fail_with(self, error: str = "boom") -> None:
            outcome["ok"] = False
            outcome["error"] = error

        def succeed(self) -> None:
            outcome["ok"] = True
            outcome["error"] = None

    return Handle()


@pytest.fixture()
def admin_user(session, make_user):
    return make_user(UserRole.admin)


@pytest.fixture()
def operator_with_warehouse_1(session, make_user):
    from app.models.warehouses import Warehouse

    op = make_user(UserRole.operator)
    op.warehouses = [session.get(Warehouse, 1)]
    session.commit()
    session.refresh(op)
    return op


def _open_alert(
    session,
    *,
    warehouse_id: int = 1,
    triggered_at: datetime | None = None,
    notified_at: datetime | None = None,
    resolved_at: datetime | None = None,
    escalated_at: datetime | None = None,
    alert_type: AlertType = AlertType.low_inventory,
) -> Alert:
    a = Alert(
        warehouse_id=warehouse_id,
        type=alert_type,
        threshold=10,
        value=0,
        triggered_at=triggered_at or datetime.now(UTC),
        notified_at=notified_at,
        resolved_at=resolved_at,
        escalated_at=escalated_at,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def _notifications_for(session, alert_id: int) -> list[AlertNotification]:
    return list(
        session.scalars(
            select(AlertNotification)
            .where(AlertNotification.alert_id == alert_id)
            .order_by(AlertNotification.sent_at, AlertNotification.id)
        )
    )


# ---------------------------------------------------------------------------
# Triggered first send
# ---------------------------------------------------------------------------


def test_triggered_email_stamps_notified_at_and_writes_audit(
    session, admin_user, operator_with_warehouse_1, fake_graph
):
    alert = _open_alert(session)

    sent = dispatch_pending_notifications(session)

    assert sent == 1
    assert len(fake_graph.calls) == 1
    session.refresh(alert)
    assert alert.notified_at is not None

    audit = _notifications_for(session, alert.id)
    assert len(audit) == 1
    assert audit[0].kind == AlertNotificationKind.triggered
    assert audit[0].ok is True
    # The operator with ACL on warehouse 1 + the admin should be on the to-list.
    addresses = audit[0].recipients.split(", ")
    assert operator_with_warehouse_1.email in addresses
    assert admin_user.email in addresses


def test_triggered_failure_retries_exactly_five_transport_attempts(
    session, admin_user, fake_graph
):
    alert = _open_alert(session)
    fake_graph.fail_with("smtp down")

    for _ in range(7):
        dispatch_pending_notifications(session)

    session.refresh(alert)
    assert alert.notified_at is None
    audit = _notifications_for(session, alert.id)
    assert len(audit) == 5
    assert len(fake_graph.calls) == 5
    assert all(row.kind == AlertNotificationKind.triggered for row in audit)
    assert all(row.ok is False and row.error == "smtp down" for row in audit)

    # Exhaustion is permanent even if transport later recovers.
    fake_graph.succeed()
    dispatch_pending_notifications(session)
    assert len(fake_graph.calls) == 5


def test_unknown_transport_crashes_still_enforce_five_reservations(
    session, admin_user, monkeypatch
):
    alert = _open_alert(session)
    invocations = {"count": 0}

    def _crash_after_reservation(**_kwargs):
        invocations["count"] += 1
        rows = _notifications_for(session, alert.id)
        assert len(rows) == invocations["count"]
        assert rows[-1].error == alerts_service._UNKNOWN_TRANSPORT_RESULT
        raise RuntimeError("simulated process loss during Graph call")

    monkeypatch.setattr(alerts_service, "send_alert_email", _crash_after_reservation)

    for expected in range(1, 6):
        with pytest.raises(RuntimeError, match="simulated process loss"):
            dispatch_pending_notifications(session)
        assert len(_notifications_for(session, alert.id)) == expected
        session.refresh(alert)
        assert alert.email_claimed_at is not None
        alert.email_claimed_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()

    # Even after the final abandoned claim becomes stale, the five durable
    # reservations exhaust the incident and no sixth transport call occurs.
    assert dispatch_pending_notifications(session) == 0
    assert invocations["count"] == 5
    assert len(_notifications_for(session, alert.id)) == 5


def test_no_recipients_releases_claim_without_consuming_attempt(
    session, monkeypatch, fake_graph
):
    alert = _open_alert(session)
    resolutions = {"count": 0}

    def _no_recipients(_db, _warehouse_id):
        resolutions["count"] += 1
        return []

    monkeypatch.setattr(
        alerts_service, "primary_recipients", _no_recipients
    )

    assert dispatch_pending_notifications(session) == 0
    assert resolutions["count"] == 1
    assert _notifications_for(session, alert.id) == []
    session.refresh(alert)
    assert alert.email_claimed_at is None

    # A later run retries the prerequisite, but the same run never hot-loops.
    assert dispatch_pending_notifications(session) == 0
    assert resolutions["count"] == 2
    assert _notifications_for(session, alert.id) == []

    monkeypatch.setattr(
        alerts_service,
        "primary_recipients",
        lambda _db, _warehouse_id: ["ops@example.com"],
    )
    assert dispatch_pending_notifications(session) == 1
    assert len(fake_graph.calls) == 1
    assert len(_notifications_for(session, alert.id)) == 1


def test_missing_warehouse_releases_claim_without_consuming_attempt(
    session, admin_user, monkeypatch, fake_graph
):
    alert = _open_alert(session)
    real_render = alerts_service.render_for_alert
    monkeypatch.setattr(
        alerts_service, "render_for_alert", lambda *_args, **_kwargs: None
    )

    assert dispatch_pending_notifications(session) == 0
    assert _notifications_for(session, alert.id) == []
    session.refresh(alert)
    assert alert.email_claimed_at is None
    assert fake_graph.calls == []

    monkeypatch.setattr(alerts_service, "render_for_alert", real_render)
    assert dispatch_pending_notifications(session) == 1
    assert len(fake_graph.calls) == 1
    assert len(_notifications_for(session, alert.id)) == 1


# ---------------------------------------------------------------------------
# No follow-up kinds
# ---------------------------------------------------------------------------


def test_aged_open_alert_does_not_send_reminder(
    session, admin_user, fake_graph
):
    long_ago = datetime.now(UTC) - timedelta(hours=48)
    alert = _open_alert(
        session,
        triggered_at=long_ago,
        notified_at=long_ago,
    )
    # Pre-existing triggered row backdated past the cadence window.
    session.add(
        AlertNotification(
            alert_id=alert.id,
            kind=AlertNotificationKind.triggered,
            sent_at=long_ago,
            recipients="someone@example.com",
            ok=True,
        )
    )
    session.commit()

    sent = dispatch_pending_notifications(session)

    assert sent == 0
    audit = _notifications_for(session, alert.id)
    assert [row.kind for row in audit] == [AlertNotificationKind.triggered]
    assert fake_graph.calls == []


def test_successful_opening_is_never_sent_twice(
    session, admin_user, fake_graph
):
    recent = datetime.now(UTC) - timedelta(hours=1)
    alert = _open_alert(
        session,
        triggered_at=recent,
        notified_at=recent,
    )
    session.add(
        AlertNotification(
            alert_id=alert.id,
            kind=AlertNotificationKind.triggered,
            sent_at=recent,
            recipients="someone@example.com",
            ok=True,
        )
    )
    session.commit()

    sent = dispatch_pending_notifications(session)

    assert sent == 0
    audit = _notifications_for(session, alert.id)
    assert all(n.kind == AlertNotificationKind.triggered for n in audit)


def test_historical_reminder_row_does_not_restart_delivery(
    session, admin_user, fake_graph
):
    long_ago = datetime.now(UTC) - timedelta(days=30)
    alert = _open_alert(
        session,
        triggered_at=long_ago,
        notified_at=long_ago,
    )
    session.add(
        AlertNotification(
            alert_id=alert.id,
            kind=AlertNotificationKind.reminder,
            sent_at=long_ago,
            recipients="someone@example.com",
            ok=True,
        )
    )
    session.commit()

    sent = dispatch_pending_notifications(session)
    assert sent == 0


# ---------------------------------------------------------------------------
# Historical escalation state
# ---------------------------------------------------------------------------


def test_aged_open_alert_does_not_escalate(
    session, admin_user, fake_graph
):
    triggered_at = datetime.now(UTC) - timedelta(hours=48)
    alert = _open_alert(
        session,
        triggered_at=triggered_at,
        notified_at=triggered_at,
    )

    dispatch_pending_notifications(session)
    session.refresh(alert)
    assert alert.escalated_at is None
    audit = _notifications_for(session, alert.id)
    assert audit == []
    assert fake_graph.calls == []


def test_historical_escalation_row_does_not_send_again(
    session, admin_user, fake_graph
):
    alert = _open_alert(
        session,
        triggered_at=datetime.now(UTC) - timedelta(hours=48),
        notified_at=datetime.now(UTC) - timedelta(hours=48),
        escalated_at=datetime.now(UTC) - timedelta(hours=24),
    )
    session.add(
        AlertNotification(
            alert_id=alert.id,
            kind=AlertNotificationKind.escalated,
            recipients="admin@example.com",
            ok=True,
        )
    )
    session.commit()

    dispatch_pending_notifications(session)

    audit = _notifications_for(session, alert.id)
    assert [row.kind for row in audit] == [AlertNotificationKind.escalated]
    assert fake_graph.calls == []


# ---------------------------------------------------------------------------
# Resolved close-out
# ---------------------------------------------------------------------------


def test_resolved_alert_never_sends_closeout(session, admin_user, fake_graph):
    alert = _open_alert(
        session,
        triggered_at=datetime.now(UTC) - timedelta(hours=2),
        notified_at=datetime.now(UTC) - timedelta(hours=2),
        resolved_at=datetime.now(UTC),
    )

    dispatch_pending_notifications(session)
    audit = _notifications_for(session, alert.id)
    assert audit == []
    assert fake_graph.calls == []


def test_resolve_before_opening_send_skips_transport(
    session, admin_user, fake_graph
):
    alert = _open_alert(session, resolved_at=datetime.now(UTC))

    assert dispatch_pending_notifications(session) == 0
    assert _notifications_for(session, alert.id) == []
    assert fake_graph.calls == []


def test_recurrence_gets_its_own_opening_email(
    session, admin_user, fake_graph
):
    first = _open_alert(
        session,
        notified_at=datetime.now(UTC) - timedelta(days=1),
        resolved_at=datetime.now(UTC) - timedelta(hours=1),
    )
    second = _open_alert(session)

    assert dispatch_pending_notifications(session) == 1
    assert _notifications_for(session, first.id) == []
    second_rows = _notifications_for(session, second.id)
    assert [row.kind for row in second_rows] == [
        AlertNotificationKind.triggered
    ]


def test_legacy_box_stuck_never_auto_emails(
    session, admin_user, fake_graph
):
    alert = _open_alert(session, alert_type=AlertType.box_stuck)

    assert dispatch_pending_notifications(session) == 0
    assert _notifications_for(session, alert.id) == []
    assert fake_graph.calls == []


def test_stale_claim_is_recovered(session, admin_user, fake_graph):
    alert = _open_alert(session)
    alert.email_claimed_at = datetime.now(UTC) - timedelta(hours=1)
    session.commit()

    assert dispatch_pending_notifications(session) == 1
    session.refresh(alert)
    assert alert.email_claimed_at is None
    assert alert.notified_at is not None


def test_fresh_claim_is_skipped_on_sqlite(
    session, admin_user, fake_graph
):
    alert = _open_alert(session)
    claimed_at = datetime.now(UTC)
    alert.email_claimed_at = claimed_at
    session.commit()

    assert dispatch_pending_notifications(session) == 0
    session.refresh(alert)
    assert alert.email_claimed_at is not None
    assert alert.notified_at is None
    assert fake_graph.calls == []


def test_stale_owner_cannot_reserve_after_takeover_at_attempt_five(
    session, admin_user, fake_graph
):
    alert = _open_alert(session)
    session.add_all(
        [
            AlertNotification(
                alert_id=alert.id,
                kind=AlertNotificationKind.triggered,
                recipients="ops@example.com",
                ok=False,
                error="failed",
            )
            for _ in range(4)
        ]
    )
    session.commit()

    old_claim = alerts_service._claim_next_opening(
        session,
        now=datetime.now(UTC) - timedelta(hours=1),
        exclude_ids=set(),
    )
    assert old_claim is not None
    new_claim = alerts_service._claim_next_opening(
        session,
        now=datetime.now(UTC),
        exclude_ids=set(),
    )
    assert new_claim is not None
    assert new_claim.token != old_claim.token

    assert alerts_service._dispatch_claimed_opening(
        session, claim=old_claim
    ) is False
    session.refresh(alert)
    assert alert.email_claimed_at is not None
    assert len(fake_graph.calls) == 0
    assert len(_notifications_for(session, alert.id)) == 4

    fake_graph.fail_with("fifth failed")
    assert alerts_service._dispatch_claimed_opening(
        session, claim=new_claim
    ) is True
    assert len(fake_graph.calls) == 1
    assert len(_notifications_for(session, alert.id)) == 5
    assert dispatch_pending_notifications(session) == 0
    assert len(fake_graph.calls) == 1


def test_stale_post_send_finalizer_cannot_clear_replacement_claim(
    session, admin_user, monkeypatch
):
    alert = _open_alert(session)
    session.add_all(
        [
            AlertNotification(
                alert_id=alert.id,
                kind=AlertNotificationKind.triggered,
                recipients="ops@example.com",
                ok=False,
                error="failed",
            )
            for _ in range(3)
        ]
    )
    session.commit()
    old_claim = alerts_service._claim_next_opening(
        session,
        now=datetime.now(UTC) - timedelta(hours=1),
        exclude_ids=set(),
    )
    assert old_claim is not None

    replacement = {"claim": None}
    transport_calls = {"count": 0}

    def _take_over_during_transport(**_kwargs):
        transport_calls["count"] += 1
        replacement["claim"] = alerts_service._claim_next_opening(
            session,
            now=datetime.now(UTC),
            exclude_ids=set(),
        )
        assert replacement["claim"] is not None
        return True, None

    monkeypatch.setattr(
        alerts_service, "send_alert_email", _take_over_during_transport
    )
    assert alerts_service._dispatch_claimed_opening(
        session, claim=old_claim
    ) is True

    new_claim = replacement["claim"]
    assert new_claim is not None
    session.refresh(alert)
    assert alert.email_claimed_at is not None
    assert alert.notified_at is None

    # The replacement owner observes the successful audit, clears only its
    # own token, and does not invoke Graph again.
    assert alerts_service._dispatch_claimed_opening(
        session, claim=new_claim
    ) is False
    session.refresh(alert)
    assert alert.email_claimed_at is None
    assert alert.notified_at is not None
    assert transport_calls["count"] == 1
    assert len(_notifications_for(session, alert.id)) == 4


def test_postgresql_claim_select_uses_skip_locked():
    stmt = alerts_service._opening_claim_select(datetime.now(UTC), set())
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FOR UPDATE SKIP LOCKED" in sql


def test_postgresql_reservation_sql_fences_claim_and_locks():
    claim = alerts_service._EmailClaim(
        alert_id=42,
        token=datetime(2026, 9, 16, 0, 0, tzinfo=UTC),
    )
    stmt = alerts_service._triggered_reservation_insert(
        claim, ["ops@example.com"]
    )
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "INSERT INTO alert_notifications" in sql
    assert "alerts.email_claimed_at =" in sql
    assert "alerts.resolved_at IS NULL" in sql
    assert "alerts.notified_at IS NULL" in sql
    assert "count(alert_notifications.id)" in sql
    assert "FOR UPDATE" in sql
