"""Productivity entry upsert, validation, and ACL gating."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.deps import get_current_user
from app.main import app
from app.models.employees import Employee, ProductivityEntry
from app.models.users import UserRole
from app.models.warehouses import Warehouse


def _impersonate(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture()
def restore_user_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def employee_w1(session) -> Employee:
    emp = Employee(
        warehouse_id=1,
        full_name="Alice",
        default_hours_per_day=Decimal("8"),
    )
    session.add(emp)
    session.commit()
    session.refresh(emp)
    return emp


def test_create_entry_succeeds(client, employee_w1):
    resp = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": employee_w1.id,
            "entry_date": "2026-05-10",
            "pages": 250,
            "hours_worked": 4.0,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pages"] == 250
    assert Decimal(body["hours_worked"]) == Decimal("4.0")
    # warehouse_id was filled in from the employee row.
    assert body["warehouse_id"] == 1


def test_post_again_for_same_day_upserts(client, session, employee_w1):
    first = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": employee_w1.id,
            "entry_date": "2026-05-10",
            "pages": 100,
            "hours_worked": 2.0,
        },
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": employee_w1.id,
            "entry_date": "2026-05-10",
            "pages": 200,
            "hours_worked": 4.0,
            "note": "corrected",
        },
    )
    assert second.status_code == 201
    body = second.json()
    # Same row updated rather than a duplicate created.
    assert body["id"] == first_id
    assert body["pages"] == 200
    assert body["note"] == "corrected"

    # Database confirms one row only.
    rows = (
        session.query(ProductivityEntry)
        .filter(
            ProductivityEntry.employee_id == employee_w1.id,
            ProductivityEntry.entry_date == date(2026, 5, 10),
        )
        .all()
    )
    assert len(rows) == 1


def test_create_entry_zero_hours_rejected(client, employee_w1):
    resp = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": employee_w1.id,
            "entry_date": "2026-05-10",
            "pages": 100,
            "hours_worked": 0,
        },
    )
    assert resp.status_code == 422


def test_create_entry_negative_pages_rejected(client, employee_w1):
    resp = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": employee_w1.id,
            "entry_date": "2026-05-10",
            "pages": -1,
            "hours_worked": 4.0,
        },
    )
    assert resp.status_code == 422


def test_inactive_employee_rejected(client, session, employee_w1):
    employee_w1.is_active = False
    session.commit()

    resp = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": employee_w1.id,
            "entry_date": "2026-05-10",
            "pages": 100,
            "hours_worked": 4.0,
        },
    )
    assert resp.status_code == 400
    assert "inactive" in resp.json()["detail"].lower()


def test_operator_cannot_post_outside_acl(
    client, session, make_user, restore_user_override
):
    # Employee in warehouse 2.
    emp = Employee(
        warehouse_id=2, full_name="Bob", default_hours_per_day=Decimal("8")
    )
    session.add(emp)
    session.commit()
    session.refresh(emp)

    # Operator only has access to warehouse 1.
    op = make_user(UserRole.operator)
    op.warehouses = [session.get(Warehouse, 1)]
    session.commit()
    session.refresh(op)
    _impersonate(op)

    resp = client.post(
        "/api/productivity/entries",
        json={
            "employee_id": emp.id,
            "entry_date": "2026-05-10",
            "pages": 50,
            "hours_worked": 2.0,
        },
    )
    # ACL miss returns 404 (matches existing pattern in box endpoints).
    assert resp.status_code == 404


def test_list_entries_scoped_by_acl(
    client, session, make_user, restore_user_override
):
    emp1 = Employee(warehouse_id=1, full_name="Alice", default_hours_per_day=Decimal("8"))
    emp2 = Employee(warehouse_id=2, full_name="Bob", default_hours_per_day=Decimal("8"))
    session.add_all([emp1, emp2])
    session.commit()
    session.refresh(emp1)
    session.refresh(emp2)

    session.add_all(
        [
            ProductivityEntry(
                employee_id=emp1.id,
                warehouse_id=1,
                entry_date=date(2026, 5, 10),
                pages=100,
                hours_worked=Decimal("4"),
            ),
            ProductivityEntry(
                employee_id=emp2.id,
                warehouse_id=2,
                entry_date=date(2026, 5, 10),
                pages=200,
                hours_worked=Decimal("4"),
            ),
        ]
    )
    session.commit()

    op = make_user(UserRole.operator)
    op.warehouses = [session.get(Warehouse, 1)]
    session.commit()
    session.refresh(op)
    _impersonate(op)

    rows = client.get("/api/productivity/entries").json()
    assert len(rows) == 1
    assert rows[0]["warehouse_id"] == 1


def test_delete_entry(client, session, employee_w1):
    entry = ProductivityEntry(
        employee_id=employee_w1.id,
        warehouse_id=1,
        entry_date=date(2026, 5, 10),
        pages=50,
        hours_worked=Decimal("2"),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    resp = client.delete(f"/api/productivity/entries/{entry.id}")
    assert resp.status_code == 204
    assert session.get(ProductivityEntry, entry.id) is None
