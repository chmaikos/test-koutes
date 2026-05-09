"""Dispatcher tests: triggered, reminder, escalation, resolved.

We monkeypatch :func:`app.services.alerts.send_alert_email` to avoid any
real Graph traffic and to capture the exact (subject, recipients) tuples
the dispatcher hands to it. Each test seeds a single open alert in a
known time window and inspects the resulting :class:`AlertNotification`
audit rows.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

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
) -> Alert:
    a = Alert(
        warehouse_id=warehouse_id,
        type=AlertType.low_inventory,
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


def test_triggered_failure_records_error_and_keeps_retrying(
    session, admin_user, fake_graph
):
    alert = _open_alert(session)
    fake_graph.fail_with("smtp down")

    dispatch_pending_notifications(session)
    session.refresh(alert)
    assert alert.notified_at is None  # NOT stamped on failure

    audit = _notifications_for(session, alert.id)
    assert len(audit) == 1
    assert audit[0].ok is False
    assert audit[0].error == "smtp down"

    # Recover and tick again -- the dispatcher should retry the same alert.
    fake_graph.succeed()
    dispatch_pending_notifications(session)
    session.refresh(alert)
    assert alert.notified_at is not None

    audit = _notifications_for(session, alert.id)
    # Two triggered rows: one failure, one success.
    assert len(audit) == 2
    assert [a.ok for a in audit] == [False, True]


# ---------------------------------------------------------------------------
# Reminder cadence
# ---------------------------------------------------------------------------


def test_reminder_only_after_window_elapses(
    session, admin_user, fake_graph, monkeypatch
):
    monkeypatch.setenv("ALERT_REMINDER_HOURS", "24")
    monkeypatch.setenv("ALERT_ESCALATION_HOURS", "0")
    get_settings.cache_clear()

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

    assert sent == 1
    audit = _notifications_for(session, alert.id)
    assert audit[-1].kind == AlertNotificationKind.reminder
    assert audit[-1].ok is True


def test_reminder_skipped_inside_window(
    session, admin_user, fake_graph, monkeypatch
):
    monkeypatch.setenv("ALERT_REMINDER_HOURS", "24")
    monkeypatch.setenv("ALERT_ESCALATION_HOURS", "0")
    get_settings.cache_clear()

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


def test_reminder_disabled_when_hours_is_zero(
    session, admin_user, fake_graph, monkeypatch
):
    monkeypatch.setenv("ALERT_REMINDER_HOURS", "0")
    monkeypatch.setenv("ALERT_ESCALATION_HOURS", "0")
    get_settings.cache_clear()

    long_ago = datetime.now(UTC) - timedelta(days=30)
    alert = _open_alert(
        session,
        triggered_at=long_ago,
        notified_at=long_ago,
    )
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


# ---------------------------------------------------------------------------
# Escalation one-shot
# ---------------------------------------------------------------------------


def test_escalation_fires_once_and_stamps_escalated_at(
    session, admin_user, fake_graph, monkeypatch
):
    monkeypatch.setenv("ALERT_REMINDER_HOURS", "0")
    monkeypatch.setenv("ALERT_ESCALATION_HOURS", "24")
    get_settings.cache_clear()

    triggered_at = datetime.now(UTC) - timedelta(hours=48)
    alert = _open_alert(
        session,
        triggered_at=triggered_at,
        notified_at=triggered_at,
    )

    dispatch_pending_notifications(session)
    session.refresh(alert)
    assert alert.escalated_at is not None
    audit = _notifications_for(session, alert.id)
    kinds = [n.kind for n in audit]
    assert AlertNotificationKind.escalated in kinds

    # Tick again -- the escalated_at column gates this; no second escalation.
    pre_count = len([n for n in audit if n.kind == AlertNotificationKind.escalated])
    dispatch_pending_notifications(session)
    audit = _notifications_for(session, alert.id)
    post_count = len([n for n in audit if n.kind == AlertNotificationKind.escalated])
    assert post_count == pre_count


def test_escalation_recipients_are_admins_only(
    session, admin_user, operator_with_warehouse_1, fake_graph, monkeypatch
):
    monkeypatch.setenv("ALERT_REMINDER_HOURS", "0")
    monkeypatch.setenv("ALERT_ESCALATION_HOURS", "24")
    get_settings.cache_clear()

    alert = _open_alert(
        session,
        triggered_at=datetime.now(UTC) - timedelta(hours=48),
        notified_at=datetime.now(UTC) - timedelta(hours=48),
    )

    dispatch_pending_notifications(session)

    audit = _notifications_for(session, alert.id)
    escalated_rows = [
        n for n in audit if n.kind == AlertNotificationKind.escalated
    ]
    assert len(escalated_rows) == 1
    addresses = escalated_rows[0].recipients.split(", ")
    assert admin_user.email in addresses
    assert operator_with_warehouse_1.email not in addresses


# ---------------------------------------------------------------------------
# Resolved close-out
# ---------------------------------------------------------------------------


def test_resolved_email_sent_once(session, admin_user, fake_graph):
    alert = _open_alert(
        session,
        triggered_at=datetime.now(UTC) - timedelta(hours=2),
        notified_at=datetime.now(UTC) - timedelta(hours=2),
        resolved_at=datetime.now(UTC),
    )

    dispatch_pending_notifications(session)
    audit = _notifications_for(session, alert.id)
    resolved_rows = [n for n in audit if n.kind == AlertNotificationKind.resolved]
    assert len(resolved_rows) == 1
    assert resolved_rows[0].ok is True

    # Second tick -- already a successful resolved row so no retry.
    dispatch_pending_notifications(session)
    audit = _notifications_for(session, alert.id)
    resolved_rows = [n for n in audit if n.kind == AlertNotificationKind.resolved]
    assert len(resolved_rows) == 1
