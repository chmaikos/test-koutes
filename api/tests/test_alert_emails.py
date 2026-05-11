"""Render-only tests for the alert email pipeline.

We hand-build ``Alert`` and ``Warehouse`` instances and inspect the
:func:`render` output -- no DB and no Graph touched here. Triggers,
recipient resolution, and the dispatcher live in their own files.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.alerts import Alert, AlertType
from app.models.warehouses import Warehouse
from app.services.alert_email import EmailKind, render


def _alert(
    alert_type: AlertType = AlertType.low_inventory,
    *,
    value: int = 0,
    threshold: int = 5,
) -> Alert:
    a = Alert(
        warehouse_id=1,
        type=alert_type,
        threshold=threshold,
        value=value,
        triggered_at=datetime(2026, 5, 9, 12, 0, tzinfo=UTC),
    )
    a.id = 42
    return a


def _warehouse() -> Warehouse:
    w = Warehouse(name="Building 1", min_inventory=5, max_capacity=100)
    w.id = 1
    return w


def test_triggered_low_inventory_subject_and_body():
    e = render(kind=EmailKind.triggered, alert=_alert(), warehouse=_warehouse())
    assert "[ACTION]" in e.subject
    assert "Building 1" in e.subject
    assert "below minimum" in e.subject.lower()
    # Severity strip label rendered into the html
    assert "Action required" in e.html
    assert "Building 1" in e.text


def test_triggered_max_capacity_subject_includes_summary():
    a = _alert(AlertType.max_capacity, value=120, threshold=100)
    e = render(kind=EmailKind.triggered, alert=a, warehouse=_warehouse())
    assert "[ACTION]" in e.subject
    assert "120/100" in e.subject
    assert "over capacity" in e.subject.lower()


def test_resolved_overrides_severity():
    e = render(kind=EmailKind.resolved, alert=_alert(), warehouse=_warehouse())
    assert "[RESOLVED]" in e.subject
    assert "Resolved" in e.html


def test_test_email_clearly_labelled():
    e = render(kind=EmailKind.test, alert=_alert(), warehouse=_warehouse())
    assert "[TEST]" in e.subject
    # The test template has an explicit "test" disclaimer.
    assert "test" in e.html.lower()
    assert "test" in e.text.lower()


def test_reminder_uses_reminder_severity_for_low_inventory():
    e = render(kind=EmailKind.reminder, alert=_alert(), warehouse=_warehouse())
    assert "[REMINDER]" in e.subject
    assert "Reminder" in e.html


def test_escalated_uses_escalated_severity():
    e = render(kind=EmailKind.escalated, alert=_alert(), warehouse=_warehouse())
    assert "[ESCALATED]" in e.subject
    assert "Escalated" in e.html


def test_near_capacity_uses_heads_up_severity():
    a = _alert(AlertType.near_capacity, value=85, threshold=90)
    e = render(kind=EmailKind.triggered, alert=a, warehouse=_warehouse())
    assert "[HEADS UP]" in e.subject
    assert "Heads up" in e.html


def test_box_stuck_summary_phrasing():
    a = _alert(AlertType.box_stuck, value=3, threshold=30)
    e = render(kind=EmailKind.triggered, alert=a, warehouse=_warehouse())
    assert "[ATTENTION]" in e.subject
    assert "stuck" in e.subject.lower()
    assert "3" in e.text


def test_recent_events_render_in_html_and_text():
    """Pre-rendered event dicts get listed in both bodies."""
    events = [
        {
            "event_type": "moved",
            "box_number": "001",
            "occurred_at": datetime(2026, 5, 9, 11, 0, tzinfo=UTC),
            "line": "Moved from Building 1 to Building 2",
        }
    ]
    e = render(
        kind=EmailKind.triggered,
        alert=_alert(),
        warehouse=_warehouse(),
        recent_events=events,
    )
    assert "001" in e.html
    # The plain-text body lists the event lines verbatim.
    assert "Moved" in e.text


def test_cta_url_only_rendered_when_base_url_present():
    """The CTA button should only appear when a public URL is configured."""
    no_cta = render(
        kind=EmailKind.triggered,
        alert=_alert(),
        warehouse=_warehouse(),
        base_url="",
    )
    with_cta = render(
        kind=EmailKind.triggered,
        alert=_alert(),
        warehouse=_warehouse(),
        base_url="https://warehouse.example.com",
    )
    assert "Open this alert" not in no_cta.html
    assert "https://warehouse.example.com/alerts/42" in with_cta.html
