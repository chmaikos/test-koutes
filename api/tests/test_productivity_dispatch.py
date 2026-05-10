"""End-of-day report dispatch: idempotency, recipient routing, no-op when graph is off."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.config import get_settings
from app.models.employees import Employee, ProductivityEntry, ProductivityReportRun
from app.services import productivity_dispatch as dispatch_module
from app.services.productivity_dispatch import send_daily_reports

REF_DATE = date(2026, 5, 13)


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Drop the lru_cache so each test sees its own monkeypatched settings."""
    yield
    get_settings.cache_clear()


@pytest.fixture()
def force_graph_configured(monkeypatch):
    """Make ``settings.graph_configured`` look True without touching env vars."""
    settings = get_settings()
    monkeypatch.setattr(
        type(settings), "graph_configured", property(lambda self: True)
    )
    return settings


@pytest.fixture()
def stub_send(monkeypatch):
    """Capture sends without hitting Microsoft Graph."""
    sent: list[dict] = []

    def _fake(*, subject, html_body, to):
        sent.append({"subject": subject, "html_body": html_body, "to": list(to)})
        return True, None

    monkeypatch.setattr(dispatch_module, "send_alert_email", _fake)
    return sent


@pytest.fixture()
def stub_send_failing(monkeypatch):
    """Pretend Graph rejects everything (recipient gets the failure recorded)."""

    def _fake(*, subject, html_body, to):
        return False, "http 500: boom"

    monkeypatch.setattr(dispatch_module, "send_alert_email", _fake)


@pytest.fixture()
def stub_recipients(monkeypatch):
    """Return a stable, deterministic recipient list."""
    monkeypatch.setattr(
        dispatch_module,
        "primary_recipients",
        lambda db, warehouse_id: [f"ops-{warehouse_id}@example.com"],
    )


def _add_entry(session, *, warehouse_id, on=REF_DATE, pages=100, hours=4):
    emp = session.query(Employee).filter(
        Employee.warehouse_id == warehouse_id
    ).first()
    if emp is None:
        emp = Employee(
            warehouse_id=warehouse_id,
            full_name=f"Employee {warehouse_id}",
            default_hours_per_day=Decimal("8"),
        )
        session.add(emp)
        session.commit()
        session.refresh(emp)
    session.add(
        ProductivityEntry(
            employee_id=emp.id,
            warehouse_id=warehouse_id,
            entry_date=on,
            pages=pages,
            hours_worked=Decimal(str(hours)),
        )
    )
    session.commit()


def test_no_op_when_graph_not_configured(client, session, stub_send):
    # Default settings have empty graph creds -> graph_configured == False.
    _add_entry(session, warehouse_id=1)
    outcome = send_daily_reports(session, on_date=REF_DATE)
    assert outcome.sent == 0
    assert stub_send == []  # send was never called


def test_skip_warehouses_with_no_entries_by_default(
    client, session, force_graph_configured, stub_send, stub_recipients
):
    # Only warehouse 1 has data; 2 and 3 should be skipped.
    _add_entry(session, warehouse_id=1)
    outcome = send_daily_reports(session, on_date=REF_DATE)
    assert outcome.sent == 1
    assert outcome.skipped_empty == 2
    assert len(stub_send) == 1
    assert stub_send[0]["to"] == ["ops-1@example.com"]


def test_include_empty_setting_sends_for_all(
    client, session, force_graph_configured, stub_send, stub_recipients, monkeypatch
):
    settings = get_settings()
    # Pydantic v2 settings expose normal attributes, so plain setattr is enough.
    monkeypatch.setattr(settings, "productivity_report_include_empty", True)
    _add_entry(session, warehouse_id=1)
    outcome = send_daily_reports(session, on_date=REF_DATE)
    # Three seeded warehouses all get a send.
    assert outcome.sent == 3
    assert {s["to"][0] for s in stub_send} == {
        "ops-1@example.com",
        "ops-2@example.com",
        "ops-3@example.com",
    }


def test_run_is_idempotent(
    client, session, force_graph_configured, stub_send, stub_recipients
):
    _add_entry(session, warehouse_id=1)
    first = send_daily_reports(session, on_date=REF_DATE)
    second = send_daily_reports(session, on_date=REF_DATE)
    assert first.sent == 1
    assert second.sent == 0
    assert second.skipped_already_sent == 1
    # Only one actual send call hit the graph stub.
    assert len(stub_send) == 1


def test_failed_send_records_run_but_allows_retry(
    client, session, force_graph_configured, stub_send_failing, stub_recipients
):
    _add_entry(session, warehouse_id=1)
    outcome = send_daily_reports(session, on_date=REF_DATE)
    assert outcome.failed == 1
    run = session.get(ProductivityReportRun, (1, REF_DATE))
    assert run is not None and run.ok is False
    assert "boom" in (run.error or "")

    # The next tick is allowed to retry because the run was not ``ok``.
    outcome2 = send_daily_reports(session, on_date=REF_DATE)
    assert outcome2.failed == 1


def test_no_recipients_records_failure(
    client, session, force_graph_configured, stub_send, monkeypatch
):
    # Empty recipient list -> the run is recorded as failed and the email
    # is not sent (so a misconfigured tenant shows up in the audit table).
    monkeypatch.setattr(
        dispatch_module, "primary_recipients", lambda db, warehouse_id: []
    )
    _add_entry(session, warehouse_id=1)
    outcome = send_daily_reports(session, on_date=REF_DATE)
    assert outcome.skipped_no_recipients == 1
    assert stub_send == []
    run = session.get(ProductivityReportRun, (1, REF_DATE))
    assert run is not None and run.ok is False


def test_recipients_routed_per_warehouse(
    client, session, force_graph_configured, stub_send, stub_recipients
):
    _add_entry(session, warehouse_id=1)
    _add_entry(session, warehouse_id=2)
    outcome = send_daily_reports(session, on_date=REF_DATE)
    assert outcome.sent == 2
    by_to = {s["to"][0]: s["subject"] for s in stub_send}
    # Each warehouse received its own per-warehouse report.
    assert "Building 1" in by_to["ops-1@example.com"]
    assert "Building 2" in by_to["ops-2@example.com"]
