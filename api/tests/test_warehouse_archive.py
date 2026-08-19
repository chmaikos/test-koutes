from __future__ import annotations

from decimal import Decimal

from app.deps import get_current_user
from app.main import app
from app.models.alerts import Alert, AlertType
from app.models.boxes import Box, BoxStatus
from app.models.employees import Employee
from app.models.lots import Lot
from app.models.requests import BoxRequest, BoxRequestDirection, BoxRequestStatus
from app.models.users import UserRole
from app.models.warehouses import Warehouse


def test_only_admin_can_archive_warehouse(client, make_user):
    operator = make_user(UserRole.operator)
    app.dependency_overrides[get_current_user] = lambda: operator

    response = client.delete("/api/warehouses/1")

    assert response.status_code == 403


def test_archive_reports_box_and_request_blockers(client, session):
    created_box = client.post(
        "/api/boxes",
        json={
            "box_number": "1",
            "lot": "ARCHIVE",
            "pallet_number": "PALLET-ARCHIVE",
            "warehouse_id": 1,
        },
    )
    assert created_box.status_code == 201

    blocked_by_box = client.delete("/api/warehouses/1")
    assert blocked_by_box.status_code == 409
    assert blocked_by_box.json()["detail"]["active_boxes"] == 1

    box = session.get(Box, created_box.json()["id"])
    assert box is not None
    box.status = BoxStatus.returned
    session.commit()
    request = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    )
    assert request.status_code == 201

    blocked_by_request = client.delete("/api/warehouses/1")
    assert blocked_by_request.status_code == 409
    assert blocked_by_request.json()["detail"]["active_requests"] == 1

    cancelled = client.post(
        f"/api/requests/{request.json()['id']}/cancel",
        json={
            "reason": "Clearing warehouse for archive",
            "expected_version": request.json()["version"],
        },
    )
    assert cancelled.status_code == 200
    detached = client.patch(
        f"/api/boxes/{box.id}",
        json={"detach_pallet": True, "note": "Preparing warehouse archive"},
    )
    assert detached.status_code == 200
    pallet_id = created_box.json()["pallet_id"]
    pallet = client.get(f"/api/pallets/{pallet_id}").json()
    archived_pallet = client.post(
        f"/api/pallets/{pallet_id}/archive",
        json={
            "reason": "Preparing warehouse archive",
            "expected_version": pallet["version"],
        },
    )
    assert archived_pallet.status_code == 200
    assert client.delete("/api/warehouses/1").status_code == 200


def test_archive_cleanup_listing_restore_and_mutation_guards(
    client, session, make_user
):
    historical_user = make_user(UserRole.operator)
    employee = Employee(
        warehouse_id=1,
        full_name="Archive Worker",
        default_hours_per_day=Decimal("8.00"),
        is_active=True,
    )
    alert = Alert(
        warehouse_id=1,
        type=AlertType.low_inventory,
        threshold=10,
        value=0,
    )
    historical_box = Box(
        box_number="099",
        lot_record=Lot(name="HISTORY"),
        current_warehouse_id=1,
        status=BoxStatus.returned,
    )
    session.add_all([employee, alert, historical_box])
    session.commit()

    archived = client.delete("/api/warehouses/1")
    assert archived.status_code == 200, archived.text
    assert archived.json()["is_active"] is False
    assert archived.json()["archived_at"] is not None
    assert archived.json()["archived_by_user_id"] is not None

    session.expire_all()
    assert session.get(Employee, employee.id).is_active is False
    assert session.get(Alert, alert.id).resolved_at is not None
    assert all(row["id"] != 1 for row in client.get("/api/warehouses").json())
    all_warehouses = client.get(
        "/api/warehouses", params={"include_inactive": True}
    ).json()
    assert next(row for row in all_warehouses if row["id"] == 1)["is_active"] is False
    dashboard_ids = {
        row["warehouse_id"]
        for row in client.get("/api/dashboard/summary").json()["warehouses"]
    }
    assert 1 not in dashboard_ids
    recipient_ids = {
        row["warehouse_id"]
        for row in client.get("/api/alerts/recipients").json()["warehouses"]
    }
    assert 1 not in recipient_ids
    assert client.get(f"/api/boxes/{historical_box.id}").status_code == 200
    assert client.get(f"/api/alerts/{alert.id}").status_code == 200
    assert (
        client.patch(
            f"/api/employees/{employee.id}",
            json={"is_active": True},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/boxes/{historical_box.id}",
            json={"contents": "must remain historical"},
        ).status_code
        == 400
    )
    delete_historical = client.post(
        f"/api/boxes/{historical_box.id}/delete",
        json={},
    )
    assert delete_historical.status_code == 400
    updated_acl = client.patch(
        f"/api/users/{historical_user.id}",
        json={"warehouse_ids": [2]},
    )
    assert updated_acl.status_code == 200
    assert set(updated_acl.json()["warehouse_ids"]) == {1, 2}
    rejected_grant = client.patch(
        f"/api/users/{historical_user.id}",
        json={"warehouse_ids": [1, 2]},
    )
    assert rejected_grant.status_code == 400

    assert (
        client.patch("/api/warehouses/1", json={"name": "Renamed"}).status_code
        == 409
    )
    assert (
        client.post(
            "/api/boxes",
            json={
                "box_number": "2",
                "lot": "BLOCKED",
                "pallet_number": "PALLET-BLOCKED",
                "warehouse_id": 1,
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/requests",
            json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/employees",
            json={
                "warehouse_id": 1,
                "full_name": "Blocked Worker",
                "default_hours_per_day": 8,
            },
        ).status_code
        == 400
    )

    restored = client.post("/api/warehouses/1/restore")
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert restored.json()["archived_at"] is None
    assert (
        client.post(
            "/api/boxes",
            json={
                "box_number": "2",
                "lot": "RESTORED",
                "pallet_number": "PALLET-RESTORED",
                "warehouse_id": 1,
            },
        ).status_code
        == 201
    )


def test_cannot_archive_last_active_warehouse(client, session):
    for warehouse_id in (2, 3):
        warehouse = session.get(Warehouse, warehouse_id)
        assert warehouse is not None
        warehouse.is_active = False
    session.commit()

    response = client.delete("/api/warehouses/1")

    assert response.status_code == 409
    assert response.json()["detail"]["last_active_warehouse"] is True


def test_return_target_blocks_archive_and_reports_target_role(client, session):
    session.add(
        BoxRequest(
            direction=BoxRequestDirection.return_,
            warehouse_id=1,
            target_warehouse_id=2,
            quantity=1,
            status=BoxRequestStatus.submitted,
        )
    )
    session.commit()

    response = client.delete("/api/warehouses/2")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["active_requests"] == 1
    assert detail["active_source_requests"] == 0
    assert detail["active_target_requests"] == 1


def test_same_source_target_request_is_counted_once(client, session):
    session.add(
        BoxRequest(
            direction=BoxRequestDirection.return_,
            warehouse_id=1,
            target_warehouse_id=1,
            quantity=1,
            status=BoxRequestStatus.submitted,
        )
    )
    session.commit()

    response = client.delete("/api/warehouses/1")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["active_requests"] == 1
    assert detail["active_source_requests"] == 1
    assert detail["active_target_requests"] == 1
