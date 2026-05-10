"""Employee CRUD, soft-delete, role gating, and ACL scoping."""
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


def test_admin_can_create_employee(client):
    resp = client.post(
        "/api/employees",
        json={
            "warehouse_id": 1,
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "default_hours_per_day": 7.5,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["full_name"] == "Jane Doe"
    assert body["warehouse_id"] == 1
    assert body["is_active"] is True
    assert Decimal(body["default_hours_per_day"]) == Decimal("7.5")


def test_create_employee_unknown_warehouse_400(client):
    resp = client.post(
        "/api/employees",
        json={"warehouse_id": 999, "full_name": "Ghost"},
    )
    assert resp.status_code == 400
    assert "999" in resp.json()["detail"]


def test_operator_cannot_create_employee(
    client, make_user, restore_user_override
):
    op = make_user(UserRole.operator)
    _impersonate(op)
    resp = client.post(
        "/api/employees",
        json={"warehouse_id": 1, "full_name": "Jane"},
    )
    assert resp.status_code == 403


def test_operator_can_list_employees_in_their_warehouses(
    client, session, make_user, restore_user_override
):
    # Seed a few employees across warehouses 1 and 2.
    for wid, name in [(1, "Alice"), (1, "Bob"), (2, "Carol")]:
        session.add(
            Employee(warehouse_id=wid, full_name=name, default_hours_per_day=Decimal("8"))
        )
    session.commit()

    op = make_user(UserRole.operator)
    op.warehouses = [session.get(Warehouse, 1)]
    session.commit()
    session.refresh(op)
    _impersonate(op)

    rows = client.get("/api/employees").json()
    names = {r["full_name"] for r in rows}
    # Carol lives in warehouse 2 which the operator can't see.
    assert names == {"Alice", "Bob"}


def test_list_filters_by_warehouse_and_inactive(client, session):
    session.add_all(
        [
            Employee(warehouse_id=1, full_name="Alice", default_hours_per_day=Decimal("8")),
            Employee(
                warehouse_id=1,
                full_name="Bob",
                default_hours_per_day=Decimal("8"),
                is_active=False,
            ),
            Employee(warehouse_id=2, full_name="Carol", default_hours_per_day=Decimal("8")),
        ]
    )
    session.commit()

    # Default: active only, all warehouses.
    rows = client.get("/api/employees").json()
    assert {r["full_name"] for r in rows} == {"Alice", "Carol"}

    # warehouse_id filter narrows to one.
    rows = client.get("/api/employees?warehouse_id=1").json()
    assert {r["full_name"] for r in rows} == {"Alice"}

    # include_inactive surfaces the soft-deleted Bob.
    rows = client.get("/api/employees?warehouse_id=1&include_inactive=true").json()
    assert {r["full_name"] for r in rows} == {"Alice", "Bob"}


def test_patch_employee_updates_fields(client, session):
    emp = Employee(
        warehouse_id=1, full_name="Alice", default_hours_per_day=Decimal("8")
    )
    session.add(emp)
    session.commit()

    resp = client.patch(
        f"/api/employees/{emp.id}",
        json={
            "full_name": "Alice Updated",
            "default_hours_per_day": 6.0,
            "warehouse_id": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["full_name"] == "Alice Updated"
    assert body["warehouse_id"] == 2
    assert Decimal(body["default_hours_per_day"]) == Decimal("6.0")


def test_delete_is_soft_and_preserves_entries(client, session):
    emp = Employee(
        warehouse_id=1, full_name="Alice", default_hours_per_day=Decimal("8")
    )
    session.add(emp)
    session.commit()

    session.add(
        ProductivityEntry(
            employee_id=emp.id,
            warehouse_id=1,
            entry_date=date(2026, 5, 10),
            pages=100,
            hours_worked=Decimal("5"),
        )
    )
    session.commit()

    resp = client.delete(f"/api/employees/{emp.id}")
    assert resp.status_code == 204

    # Employee row still exists (soft delete) with is_active = False.
    refreshed = session.get(Employee, emp.id)
    assert refreshed is not None
    assert refreshed.is_active is False

    # The historical productivity entry survived.
    entries = (
        session.query(ProductivityEntry)
        .filter(ProductivityEntry.employee_id == emp.id)
        .all()
    )
    assert len(entries) == 1


def test_delete_unknown_employee_404(client):
    resp = client.delete("/api/employees/999")
    assert resp.status_code == 404
