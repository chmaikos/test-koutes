"""Tests for admin force-override and admin-only delete endpoints."""
from __future__ import annotations


def _create_box(client, *, box_number: str, warehouse_id: int = 1, owner: str = "x") -> int:
    resp = client.post(
        "/api/boxes",
        json={"box_number": box_number, "owner": owner, "warehouse_id": warehouse_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _advance(client, box_id: int, *statuses: str) -> None:
    for s in statuses:
        resp = client.patch(f"/api/boxes/{box_id}", json={"status": s})
        assert resp.status_code == 200, resp.text


def _impersonate(role):
    """Swap the dependency override so subsequent requests run as `role`."""
    from app.deps import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: role


# ---------------------------------------------------------------------------
# force override on single PATCH
# ---------------------------------------------------------------------------


def test_force_lets_admin_resurrect_returned_box(client):
    box_id = _create_box(client, box_number="F-1")
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

    events = client.get(f"/api/boxes/{box_id}/events").json()
    forced_event = next(
        ev
        for ev in events
        if ev["from_status"] == "returned" and ev["to_status"] == "received"
    )
    assert forced_event["note"].startswith("[admin override]")
    assert "mistake fix" in forced_event["note"]


def test_force_lets_admin_move_returned_box(client):
    box_id = _create_box(client, box_number="F-2", warehouse_id=1)
    _advance(client, box_id, "ready_to_return", "returned")

    rejected = client.patch(f"/api/boxes/{box_id}", json={"warehouse_id": 2})
    assert rejected.status_code == 400

    forced = client.patch(
        f"/api/boxes/{box_id}", json={"warehouse_id": 2, "force": True}
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["current_warehouse_id"] == 2


def test_force_requires_admin(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="F-3")
    _advance(client, box_id, "ready_to_return", "returned")

    operator = make_user(UserRole.operator)
    _impersonate(operator)
    try:
        resp = client.patch(
            f"/api/boxes/{box_id}",
            json={"status": "received", "force": True},
        )
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()
    finally:
        from app.deps import get_current_user
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)


def test_force_default_false_still_enforces_rules(client):
    """Regression: omitting force keeps the original 400 behaviour."""
    box_id = _create_box(client, box_number="F-4")
    _advance(client, box_id, "ready_to_return", "returned")

    resp = client.patch(f"/api/boxes/{box_id}", json={"status": "received"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# force override on bulk
# ---------------------------------------------------------------------------


def test_bulk_force_overrides_returned(client):
    active = _create_box(client, box_number="BF-1")
    returned = _create_box(client, box_number="BF-2")
    _advance(client, returned, "ready_to_return", "returned")

    without_force = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [active, returned], "warehouse_id": 2},
    )
    body = without_force.json()
    assert {b["id"] for b in body["updated"]} == {active}
    assert len(body["skipped"]) == 1

    # Move the active one back so we have something to re-move forcibly.
    client.patch(f"/api/boxes/{active}", json={"warehouse_id": 1})

    forced = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [active, returned], "warehouse_id": 3, "force": True},
    )
    assert forced.status_code == 200, forced.text
    forced_body = forced.json()
    assert {b["id"] for b in forced_body["updated"]} == {active, returned}
    assert forced_body["skipped"] == []
    assert all(b["current_warehouse_id"] == 3 for b in forced_body["updated"])


def test_bulk_force_requires_admin(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="BF-3")
    operator = make_user(UserRole.operator)
    _impersonate(operator)
    try:
        resp = client.post(
            "/api/boxes/bulk",
            json={"box_ids": [box_id], "status": "returned", "force": True},
        )
        assert resp.status_code == 403
    finally:
        from app.deps import get_current_user
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_admin_can_delete_box_and_history_vanishes(client):
    box_id = _create_box(client, box_number="D-1")
    _advance(client, box_id, "ready_to_return")

    resp = client.delete(f"/api/boxes/{box_id}")
    assert resp.status_code == 204

    assert client.get(f"/api/boxes/{box_id}").status_code == 404
    assert client.get(f"/api/boxes/{box_id}/events").status_code == 404


def test_delete_missing_returns_404(client):
    resp = client.delete("/api/boxes/999999")
    assert resp.status_code == 404


def test_operator_cannot_delete(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="D-2")
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
    a = _create_box(client, box_number="BD-1")
    b = _create_box(client, box_number="BD-2")

    resp = client.post(
        "/api/boxes/bulk-delete",
        json={"box_ids": [a, 999_999, b]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["deleted_ids"]) == sorted([a, b])
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["box_id"] == 999_999

    # Both real boxes are gone.
    assert client.get(f"/api/boxes/{a}").status_code == 404
    assert client.get(f"/api/boxes/{b}").status_code == 404


def test_bulk_delete_requires_admin(client, make_user):
    from app.models.users import UserRole

    box_id = _create_box(client, box_number="BD-3")
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
    a = _create_box(client, box_number="DD-1")
    resp = client.post(
        "/api/boxes/bulk-delete", json={"box_ids": [a, a, a]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_ids"] == [a]
    assert body["skipped"] == []
