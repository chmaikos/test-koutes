"""Aggregation math: weighted pages-per-hour, top/bottom rankings, weekly window.

These tests exercise the service-layer helpers directly; the HTTP layer
just wraps them. Anchoring the date inputs to a fixed Wednesday makes
ISO-week assertions stable regardless of when the suite runs.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.employees import Employee, ProductivityEntry
from app.services.productivity import daily_summary, week_bounds, weekly_summary

REF_DATE = date(2026, 5, 13)  # Wednesday
WEEK_MONDAY = date(2026, 5, 11)
WEEK_SUNDAY = date(2026, 5, 17)


@pytest.fixture()
def warehouse_with_employees(session):
    """Three employees in warehouse 1, plus one in warehouse 2."""
    rows = [
        Employee(warehouse_id=1, full_name="Alice", default_hours_per_day=Decimal("8")),
        Employee(warehouse_id=1, full_name="Bob", default_hours_per_day=Decimal("8")),
        Employee(warehouse_id=1, full_name="Carol", default_hours_per_day=Decimal("8")),
        Employee(warehouse_id=2, full_name="Dave", default_hours_per_day=Decimal("8")),
    ]
    session.add_all(rows)
    session.commit()
    for r in rows:
        session.refresh(r)
    return rows


def _add_entry(session, *, employee, on, pages, hours, warehouse_id=None):
    session.add(
        ProductivityEntry(
            employee_id=employee.id,
            warehouse_id=warehouse_id or employee.warehouse_id,
            entry_date=on,
            pages=pages,
            hours_worked=Decimal(str(hours)),
        )
    )


def test_week_bounds_snaps_to_monday():
    monday, sunday = week_bounds(REF_DATE)
    assert monday == WEEK_MONDAY
    assert sunday == WEEK_SUNDAY


def test_daily_summary_weighted_pages_per_hour(session, warehouse_with_employees):
    alice, bob, _carol, _dave = warehouse_with_employees
    # Alice: 100p / 1h = 100 p/h; Bob: 100p / 9h ~= 11.11 p/h.
    # Mean of ratios would be ~55.5; weighted is 200 / 10 = 20.
    _add_entry(session, employee=alice, on=REF_DATE, pages=100, hours=1)
    _add_entry(session, employee=bob, on=REF_DATE, pages=100, hours=9)
    session.commit()

    summary = daily_summary(session, on_date=REF_DATE)
    w1 = summary[1]
    assert w1.total_pages == 200
    assert w1.total_hours == 10.0
    # Weighted pages-per-hour: sum(pages) / sum(hours) = 200/10 = 20.0.
    assert w1.avg_pages_per_hour == 20.0
    assert w1.entry_count == 2


def test_top_and_bottom_ordering(session, warehouse_with_employees):
    alice, bob, carol, _dave = warehouse_with_employees
    # Pages-per-hour: Alice=100, Bob=10, Carol=50.
    _add_entry(session, employee=alice, on=REF_DATE, pages=100, hours=1)
    _add_entry(session, employee=bob, on=REF_DATE, pages=10, hours=1)
    _add_entry(session, employee=carol, on=REF_DATE, pages=50, hours=1)
    session.commit()

    summary = daily_summary(session, on_date=REF_DATE)
    w1 = summary[1]
    top_names = [p.employee_name for p in w1.top]
    bottom_names = [p.employee_name for p in w1.bottom]
    # Top: highest p/h first.
    assert top_names == ["Alice", "Carol", "Bob"]
    # Bottom: worst (lowest p/h) first.
    assert bottom_names[0] == "Bob"


def test_tie_break_prefers_more_pages_then_name(session, warehouse_with_employees):
    alice, bob, carol, _dave = warehouse_with_employees
    # Same pages-per-hour (50). Carol has more pages so wins the tie.
    # Alice and Bob are then tied on both p/h and pages -> alphabetic.
    _add_entry(session, employee=alice, on=REF_DATE, pages=50, hours=1)
    _add_entry(session, employee=bob, on=REF_DATE, pages=50, hours=1)
    _add_entry(session, employee=carol, on=REF_DATE, pages=100, hours=2)
    session.commit()

    summary = daily_summary(session, on_date=REF_DATE)
    top_names = [p.employee_name for p in summary[1].top]
    assert top_names == ["Carol", "Alice", "Bob"]


def test_inactive_employees_excluded_from_rankings_but_count_in_totals(
    session, warehouse_with_employees
):
    alice, bob, _carol, _dave = warehouse_with_employees
    bob.is_active = False
    session.commit()

    _add_entry(session, employee=alice, on=REF_DATE, pages=20, hours=1)
    _add_entry(session, employee=bob, on=REF_DATE, pages=200, hours=1)
    session.commit()

    summary = daily_summary(session, on_date=REF_DATE)
    w1 = summary[1]
    # Both entries contribute to totals.
    assert w1.total_pages == 220
    # Bob is inactive so absent from rankings even though his p/h would top.
    top_names = [p.employee_name for p in w1.top]
    assert "Bob" not in top_names
    assert top_names == ["Alice"]


def test_acl_scoping(session, warehouse_with_employees, make_user):
    from app.models.warehouses import Warehouse

    alice, _bob, _carol, dave = warehouse_with_employees
    _add_entry(session, employee=alice, on=REF_DATE, pages=100, hours=1)
    _add_entry(session, employee=dave, on=REF_DATE, pages=200, hours=1)
    session.commit()

    op = make_user()
    op.warehouses = [session.get(Warehouse, 2)]
    session.commit()
    session.refresh(op)

    summary = daily_summary(session, user=op, on_date=REF_DATE)
    # Only warehouse 2 should appear.
    assert list(summary.keys()) == [2]


def test_weekly_summary_sums_all_days_in_week(
    session, warehouse_with_employees
):
    alice, *_ = warehouse_with_employees
    # Three days inside the week, one outside.
    _add_entry(session, employee=alice, on=WEEK_MONDAY, pages=10, hours=1)
    _add_entry(session, employee=alice, on=REF_DATE, pages=20, hours=1)
    _add_entry(session, employee=alice, on=WEEK_SUNDAY, pages=30, hours=1)
    _add_entry(
        session,
        employee=alice,
        on=date(2026, 5, 18),  # next Monday
        pages=999,
        hours=1,
    )
    session.commit()

    weekly = weekly_summary(session, week_start=REF_DATE)
    w1 = weekly[1]
    assert w1.total_pages == 60
    assert w1.total_hours == 3.0
    assert w1.avg_pages_per_hour == 20.0


def test_active_employees_count_independent_of_entries(
    session, warehouse_with_employees
):
    summary = daily_summary(session, on_date=REF_DATE)
    # No entries -> no warehouse keys returned, but a single-entry day
    # should report active_employees from the roster.
    alice, *_ = warehouse_with_employees
    _add_entry(session, employee=alice, on=REF_DATE, pages=10, hours=1)
    session.commit()
    summary = daily_summary(session, on_date=REF_DATE)
    # Three active employees in warehouse 1 even though only Alice logged.
    assert summary[1].active_employees == 3


def test_dashboard_summary_includes_productivity(client, session, warehouse_with_employees):
    alice, *_ = warehouse_with_employees
    today = date.today()
    _add_entry(session, employee=alice, on=today, pages=80, hours=4)
    session.commit()

    body = client.get("/api/dashboard/summary").json()
    w1 = next(w for w in body["warehouses"] if w["warehouse_id"] == 1)
    assert w1["productivity_today"] is not None
    assert w1["productivity_today"]["total_pages"] == 80
    assert w1["productivity_today"]["avg_pages_per_hour"] == 20.0
