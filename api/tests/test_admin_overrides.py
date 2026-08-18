"""Tests for admin force-override and admin-only delete endpoints."""
from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.models.boxes import Box
from app.models.requests import (
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
    BoxRequestStatus,
)
from app.models.warehouses import Warehouse
from app.services.boxes import _active_return_request_stmt


def test_active_return_lock_query_is_supported_by_postgresql():
    sql = str(
        _active_return_request_stmt(49).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "FOR UPDATE" in sql
    assert "DISTINCT" not in sql


def _create_box(client, *, box_number: str, warehouse_id: int = 1, lot: str = "x") -> int:
    resp = client.post(
        "/api/boxes",
        json={"box_number": box_number, "lot": lot, "warehouse_id": warehouse_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# Canonical forward chain. ``_advance`` walks intermediate states so a test
# that wants to land on (say) ``returned`` doesn't have to spell out every
# single step explicitly each time -- it just declares the milestones.
_FORWARD_CHAIN = (
    "received",
    "processing",
    "incomplete",
    "ready_to_return",
    "returned",
)


def _advance(client, box_id: int, *statuses: str) -> None:
    """Walk a box forward through each requested status milestone.

    ``statuses`` are *destinations*; the helper fills in the intermediate
    transitions implied by the linear chain so callers stay terse and any
    future state machine changes are absorbed in one place.
    """
    for target in statuses:
        current = client.get(f"/api/boxes/{box_id}").json()["status"]
        start = _FORWARD_CHAIN.index(current)
        end = _FORWARD_CHAIN.index(target)
        for step in _FORWARD_CHAIN[start + 1 : end + 1]:
            resp = client.patch(f"/api/boxes/{box_id}", json={"status": step})
            assert resp.status_code == 200, resp.text


def _impersonate(role):
    """Swap the dependency override so subsequent requests run as `role`."""
    from app.deps import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: role


def _reserve_return(session, box_id: int) -> int:
    box = session.get(Box, box_id)
    request = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=box.current_warehouse_id,
        target_warehouse_id=box.current_warehouse_id,
        quantity=1,
        status=BoxRequestStatus.submitted,
    )
    session.add(request)
    session.flush()
    request.root_request_id = request.id
    session.add(
        BoxRequestItem(
            request_id=request.id,
            position=1,
            box_id=box.id,
            lot_id=box.lot_id,
            lot=box.lot,
            box_number=box.box_number,
            contents=box.contents,
        )
    )
    session.commit()
    return request.id


# ---------------------------------------------------------------------------
# force override on single PATCH
# ---------------------------------------------------------------------------


def test_force_lets_admin_resurrect_returned_box_and_clears_timestamp(client):
    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "ready_to_return", "returned")

    no_force = client.patch(
        f"/api/boxes/{box_id}", json={"status": "received"}
    )
    assert no_force.status_code == 400

    forced = client.patch(
        f"/api/boxes/{box_id}",
        json={"status": "received", "force": True, "note": "mistake fix"},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["status"] == "received"
    assert forced.json()["returned_at"] is None

    events = client.get(f"/api/boxes/{box_id}/events").json()
    forced_event = next(
        ev
        for ev in events
        if ev["from_status"] == "returned" and ev["to_status"] == "received"
    )
    assert forced_event["note"].startswith("[admin override]")
    assert "mistake fix" in forced_event["note"]


def test_force_lets_admin_move_returned_box(client, session, monkeypatch):
    box_id = _create_box(client, box_number="001", warehouse_id=1)
    _advance(client, box_id, "ready_to_return", "returned")
    returned_at = client.get(f"/api/boxes/{box_id}").json()["returned_at"]
    reservation_id = _reserve_return(session, box_id)

    rejected = client.patch(f"/api/boxes/{box_id}", json={"warehouse_id": 2})
    assert rejected.status_code == 409

    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr("app.routers.boxes.bus.publish", capture)
    forced = client.patch(
        f"/api/boxes/{box_id}",
        json={
            "warehouse_id": 2,
            "force": True,
            "note": "Correcting physical location",
        },
    )
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["current_warehouse_id"] == 2
    assert body["status"] == "returned"
    assert body["returned_at"] == returned_at
    assert body["cancelled_request_ids"] == [reservation_id]
    assert session.get(BoxRequest, reservation_id).status == BoxRequestStatus.cancelled

    events = client.get(f"/api/boxes/{box_id}/events").json()
    moved = next(event for event in events if event["event_type"] == "moved")
    assert moved["from_warehouse_id"] == 1
    assert moved["to_warehouse_id"] == 2
    assert moved["user_id"] is not None
    assert moved["metadata"]["admin_override"] is True
    assert moved["metadata"]["override_reason"] == "Correcting physical location"
    assert reservation_id in moved["metadata"]["linked_request_ids"]
    assert moved["metadata"]["cancelled_request_ids"] == [reservation_id]
    assert {
        payload["warehouse_id"]
        for event_type, payload in published
        if event_type == "box.updated"
    } == {1, 2}


def test_force_requires_admin(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "ready_to_return", "returned")

    operator = make_user(UserRole.operator)
    _impersonate(operator)
    try:
        resp = client.patch(
            f"/api/boxes/{box_id}",
            json={
                "warehouse_id": 2,
                "force": True,
                "note": "Physical correction",
            },
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()
    finally:
        from app.deps import get_current_user
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)


def test_force_default_false_still_enforces_rules(client):
    """Regression: omitting force keeps the original 400 behaviour."""
    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "ready_to_return", "returned")

    resp = client.patch(f"/api/boxes/{box_id}", json={"status": "received"})
    assert resp.status_code == 400


def test_force_override_requires_reason(client):
    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "returned")
    move = client.patch(
        f"/api/boxes/{box_id}",
        json={"warehouse_id": 2, "force": True, "note": "   "},
    )
    assert move.status_code == 400
    archive = client.post(
        f"/api/boxes/{box_id}/delete",
        json={"force": True},
    )
    assert archive.status_code == 400


def test_returned_relocation_rejects_archived_target(client, session):
    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "returned")
    target = session.get(Warehouse, 2)
    target.is_active = False
    session.commit()

    response = client.patch(
        f"/api/boxes/{box_id}",
        json={
            "warehouse_id": 2,
            "force": True,
            "note": "Physical correction",
        },
    )
    assert response.status_code == 400
    assert "archived" in response.json()["detail"]
    assert client.get(f"/api/boxes/{box_id}").json()["current_warehouse_id"] == 1


def test_entering_returned_sets_timestamp_when_missing(client, session):
    box_id = _create_box(client, box_number="001")
    box = session.get(Box, box_id)
    box.returned_at = None
    session.commit()

    _advance(client, box_id, "returned")
    assert client.get(f"/api/boxes/{box_id}").json()["returned_at"] is not None


# ---------------------------------------------------------------------------
# force override on bulk
# ---------------------------------------------------------------------------


def test_bulk_force_overrides_returned_and_reports_cancelled_ids(
    client, session, monkeypatch
):
    active = _create_box(client, box_number="001")
    returned = _create_box(client, box_number="002")
    _advance(client, returned, "ready_to_return", "returned")
    returned_at = client.get(f"/api/boxes/{returned}").json()["returned_at"]
    reservation_id = _reserve_return(session, returned)

    without_force = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [active, returned], "warehouse_id": 2},
    )
    body = without_force.json()
    assert body["updated"] == []
    assert {skip["box_id"] for skip in body["skipped"]} == {active, returned}

    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr("app.routers.boxes.bus.publish", capture)
    forced = client.post(
        "/api/boxes/bulk",
        json={
            "box_ids": [active, returned],
            "warehouse_id": 3,
            "force": True,
            "note": "Correcting physical locations",
        },
    )
    assert forced.status_code == 200, forced.text
    forced_body = forced.json()
    assert {b["id"] for b in forced_body["updated"]} == {active, returned}
    assert forced_body["skipped"] == []
    assert all(b["current_warehouse_id"] == 3 for b in forced_body["updated"])
    returned_result = next(box for box in forced_body["updated"] if box["id"] == returned)
    assert returned_result["status"] == "returned"
    assert returned_result["returned_at"] == returned_at
    assert forced_body["cancelled_request_ids"] == [reservation_id]
    assert {
        payload["warehouse_id"]
        for event_type, payload in published
        if event_type == "box.updated"
    } == {1, 3}


def test_bulk_force_requires_admin(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="001")
    operator = make_user(UserRole.operator)
    _impersonate(operator)
    try:
        resp = client.post(
            "/api/boxes/bulk",
            json={
                "box_ids": [box_id],
                "warehouse_id": 2,
                "force": True,
                "note": "Physical correction",
            },
        )
        assert resp.status_code == 403
    finally:
        from app.deps import get_current_user
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)


def test_bulk_returned_relocation_requires_reason_and_active_target(client, session):
    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "returned")

    missing_reason = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [box_id], "warehouse_id": 2, "force": True},
    )
    assert missing_reason.status_code == 400

    session.get(Warehouse, 2).is_active = False
    session.commit()
    inactive_target = client.post(
        "/api/boxes/bulk",
        json={
            "box_ids": [box_id],
            "warehouse_id": 2,
            "force": True,
            "note": "Physical correction",
        },
    )
    assert inactive_target.status_code == 200
    assert inactive_target.json()["updated"] == []
    assert inactive_target.json()["skipped"][0]["box_id"] == box_id
    assert "archived" in inactive_target.json()["skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_linked_box_requires_force_and_is_archived(client):
    box_id = _create_box(client, box_number="001")
    _advance(client, box_id, "ready_to_return")

    resp = client.post(f"/api/boxes/{box_id}/delete", json={})
    assert resp.status_code == 409

    archived = client.post(
        f"/api/boxes/{box_id}/delete",
        json={"force": True, "reason": "Duplicate physical record"},
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    detail = client.get(f"/api/boxes/{box_id}")
    assert detail.status_code == 200
    assert detail.json()["archived_at"] is not None
    assert detail.json()["archive_reason"] == "Duplicate physical record"
    events = client.get(f"/api/boxes/{box_id}/events").json()
    assert events[0]["event_type"] == "archived"
    listed_ids = {
        item["id"] for item in client.get("/api/boxes").json()["items"]
    }
    assert box_id not in listed_ids


def test_delete_missing_returns_404(client):
    resp = client.delete("/api/boxes/999999")
    assert resp.status_code == 404


def test_operator_cannot_delete(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="001")
    operator = make_user(UserRole.operator)
    _impersonate(operator)
    try:
        resp = client.delete(f"/api/boxes/{box_id}")
        assert resp.status_code == 403
    finally:
        from app.deps import get_current_user
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)


def test_bulk_delete_with_missing_id(client):
    a = _create_box(client, box_number="001")
    b = _create_box(client, box_number="002")

    resp = client.post(
        "/api/boxes/bulk-delete",
        json={
            "box_ids": [a, 999_999, b],
            "force": True,
            "reason": "Duplicate imported records",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_ids"] == []
    assert sorted(body["archived_ids"]) == sorted([a, b])
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["box_id"] == 999_999

    assert client.get(f"/api/boxes/{a}").json()["archived_at"] is not None
    assert client.get(f"/api/boxes/{b}").json()["archived_at"] is not None


def test_bulk_delete_requires_admin(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="001")
    operator = make_user(UserRole.operator)
    _impersonate(operator)
    try:
        resp = client.post(
            "/api/boxes/bulk-delete", json={"box_ids": [box_id]}
        )
        assert resp.status_code == 403
    finally:
        from app.deps import get_current_user
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)


def test_bulk_delete_dedupes_ids(client):
    a = _create_box(client, box_number="001")
    resp = client.post(
        "/api/boxes/bulk-delete",
        json={
            "box_ids": [a, a, a],
            "force": True,
            "reason": "Duplicate record",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_ids"] == []
    assert body["archived_ids"] == [a]
    assert body["skipped"] == []
