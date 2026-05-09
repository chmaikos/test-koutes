"""Per-warehouse ACL tests.

Covers admin bypass, the deliberate "no rows = no access" default for new
non-admin users, partial grants, blocked writes (create + move), filtering
on alerts/dashboard, the admin grant flow via PATCH /users/{id}, and the
SSE payload filter.
"""
from __future__ import annotations

import pytest

from app.deps import get_current_user
from app.main import app


def _impersonate(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture()
def operator_with_no_access(client, session, make_user):
    """An operator with the per-warehouse ACL deliberately cleared.

    `make_user` mirrors the production migration backfill (every existing
    warehouse), so we explicitly drop the rows here to exercise the
    "default no access" rule that newly provisioned users land on.
    """
    from app.models.users import UserRole

    op = make_user(UserRole.operator)
    op.warehouses = []
    session.commit()
    session.refresh(op)
    return op


@pytest.fixture()
def operator_with_access_to_two(client, session, make_user):
    from app.models.users import UserRole
    from app.models.warehouses import Warehouse

    op = make_user(UserRole.operator)
    op.warehouses = [session.get(Warehouse, 2)]
    session.commit()
    session.refresh(op)
    return op


@pytest.fixture()
def restore_user_override():
    """Drops the in-test impersonation so other tests get the admin again."""
    yield
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Admin bypass
# ---------------------------------------------------------------------------


def test_admin_sees_every_warehouse_and_every_box(client):
    # Seed boxes across two warehouses while running as admin.
    for payload in (
        {"box_number": "A-1", "lot": "x", "warehouse_id": 1},
        {"box_number": "A-2", "lot": "x", "warehouse_id": 2},
        {"box_number": "A-3", "lot": "x", "warehouse_id": 3},
    ):
        assert client.post("/api/boxes", json=payload).status_code == 201

    warehouses = client.get("/api/warehouses").json()
    assert {w["id"] for w in warehouses} == {1, 2, 3}

    boxes = client.get("/api/boxes").json()
    assert boxes["total"] == 3

    summary = client.get("/api/dashboard/summary").json()
    assert {w["warehouse_id"] for w in summary["warehouses"]} == {1, 2, 3}


# ---------------------------------------------------------------------------
# Default new user has no access
# ---------------------------------------------------------------------------


def test_default_new_user_sees_nothing(
    client, operator_with_no_access, restore_user_override
):
    # Admin seeds a box in warehouse 1.
    create = client.post(
        "/api/boxes",
        json={"box_number": "N-1", "lot": "x", "warehouse_id": 1},
    )
    assert create.status_code == 201
    box_id = create.json()["id"]

    _impersonate(operator_with_no_access)

    assert client.get("/api/warehouses").json() == []
    assert client.get("/api/boxes").json()["total"] == 0
    # Existence is not leaked: ACL miss returns 404, not 403.
    assert client.get(f"/api/boxes/{box_id}").status_code == 404
    assert client.get(f"/api/boxes/{box_id}/events").status_code == 404
    assert client.get("/api/dashboard/summary").json()["warehouses"] == []
    blocked = client.post(
        "/api/boxes",
        json={"box_number": "N-2", "lot": "x", "warehouse_id": 1},
    )
    assert blocked.status_code == 403


# ---------------------------------------------------------------------------
# Partial grants
# ---------------------------------------------------------------------------


def test_partial_grant_filters_lists_and_aggregates(
    client, operator_with_access_to_two, restore_user_override
):
    for payload in (
        {"box_number": "P-1", "lot": "x", "warehouse_id": 1},
        {"box_number": "P-2", "lot": "x", "warehouse_id": 2},
        {"box_number": "P-3", "lot": "x", "warehouse_id": 3},
    ):
        assert client.post("/api/boxes", json=payload).status_code == 201

    _impersonate(operator_with_access_to_two)

    warehouses = client.get("/api/warehouses").json()
    assert [w["id"] for w in warehouses] == [2]

    boxes = client.get("/api/boxes").json()
    assert boxes["total"] == 1
    assert boxes["items"][0]["box_number"] == "P-2"

    summary = client.get("/api/dashboard/summary").json()
    assert [w["warehouse_id"] for w in summary["warehouses"]] == [2]


# ---------------------------------------------------------------------------
# Blocked writes
# ---------------------------------------------------------------------------


def test_create_in_forbidden_warehouse_is_403(
    client, operator_with_access_to_two, restore_user_override
):
    _impersonate(operator_with_access_to_two)

    forbidden = client.post(
        "/api/boxes",
        json={"box_number": "X-1", "lot": "x", "warehouse_id": 1},
    )
    assert forbidden.status_code == 403

    allowed = client.post(
        "/api/boxes",
        json={"box_number": "X-2", "lot": "x", "warehouse_id": 2},
    )
    assert allowed.status_code == 201


def test_move_into_forbidden_warehouse_is_403(
    client, operator_with_access_to_two, restore_user_override
):
    create = client.post(
        "/api/boxes",
        json={"box_number": "M-1", "lot": "x", "warehouse_id": 2},
    )
    assert create.status_code == 201
    box_id = create.json()["id"]

    _impersonate(operator_with_access_to_two)

    blocked = client.patch(
        f"/api/boxes/{box_id}", json={"warehouse_id": 1}
    )
    assert blocked.status_code == 403


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def test_alerts_filtered_and_ack_blocked(
    client, session, operator_with_access_to_two, restore_user_override
):
    from datetime import UTC, datetime

    from app.models.alerts import Alert, AlertType

    visible = Alert(
        warehouse_id=2,
        type=AlertType.low_inventory,
        threshold=5,
        value=0,
        triggered_at=datetime.now(UTC),
    )
    hidden = Alert(
        warehouse_id=1,
        type=AlertType.low_inventory,
        threshold=5,
        value=0,
        triggered_at=datetime.now(UTC),
    )
    session.add_all([visible, hidden])
    session.commit()
    session.refresh(visible)
    session.refresh(hidden)

    _impersonate(operator_with_access_to_two)

    listing = client.get("/api/alerts").json()
    assert {a["id"] for a in listing} == {visible.id}

    blocked_ack = client.post(f"/api/alerts/{hidden.id}/ack")
    assert blocked_ack.status_code == 404

    allowed_ack = client.post(f"/api/alerts/{visible.id}/ack")
    assert allowed_ack.status_code == 200


# ---------------------------------------------------------------------------
# Alert detail + test-email endpoints
# ---------------------------------------------------------------------------


def test_alert_detail_acl_gated(
    client, session, operator_with_access_to_two, restore_user_override
):
    """GET /alerts/{id} returns 404 (not 403) for cross-warehouse alerts."""
    from datetime import UTC, datetime

    from app.models.alerts import (
        Alert,
        AlertNotification,
        AlertNotificationKind,
        AlertType,
    )

    mine = Alert(
        warehouse_id=2,
        type=AlertType.low_inventory,
        threshold=5,
        value=0,
        triggered_at=datetime.now(UTC),
    )
    elsewhere = Alert(
        warehouse_id=1,
        type=AlertType.low_inventory,
        threshold=5,
        value=0,
        triggered_at=datetime.now(UTC),
    )
    session.add_all([mine, elsewhere])
    session.commit()
    session.refresh(mine)
    session.refresh(elsewhere)
    # Add a couple of notification rows so we exercise the embed too.
    session.add_all(
        [
            AlertNotification(
                alert_id=mine.id,
                kind=AlertNotificationKind.triggered,
                recipients="ops@example.com",
                ok=True,
            ),
        ]
    )
    session.commit()

    _impersonate(operator_with_access_to_two)

    ok = client.get(f"/api/alerts/{mine.id}")
    assert ok.status_code == 200
    body = ok.json()
    assert body["alert"]["id"] == mine.id
    assert len(body["notifications"]) == 1
    assert body["notifications"][0]["kind"] == "triggered"

    blocked = client.get(f"/api/alerts/{elsewhere.id}")
    assert blocked.status_code == 404


def test_test_email_endpoint_is_admin_only(
    client, session, operator_with_access_to_two, restore_user_override, monkeypatch
):
    from datetime import UTC, datetime

    from app.models.alerts import Alert, AlertType
    from app.services import alerts as alerts_service

    # Stub the Graph send so admins running this test don't try to call out.
    monkeypatch.setattr(
        alerts_service,
        "send_alert_email",
        lambda *, subject, html_body, to=None: (True, None),
    )

    a = Alert(
        warehouse_id=2,
        type=AlertType.low_inventory,
        threshold=5,
        value=0,
        triggered_at=datetime.now(UTC),
    )
    session.add(a)
    session.commit()
    session.refresh(a)

    _impersonate(operator_with_access_to_two)
    forbidden = client.post(f"/api/alerts/{a.id}/test-email")
    assert forbidden.status_code == 403


def test_admin_test_email_records_test_kind_audit_row(client, session, monkeypatch):
    """Admins can hit the test-email path; the audit row uses ``kind=test``
    so the reminder/escalation cadence is unaffected."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.models.alerts import Alert, AlertNotification, AlertType
    from app.services import alerts as alerts_service

    monkeypatch.setattr(
        alerts_service,
        "send_alert_email",
        lambda *, subject, html_body, to=None: (True, None),
    )

    a = Alert(
        warehouse_id=1,
        type=AlertType.low_inventory,
        threshold=5,
        value=0,
        triggered_at=datetime.now(UTC),
    )
    session.add(a)
    session.commit()
    session.refresh(a)

    resp = client.post(f"/api/alerts/{a.id}/test-email")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["recipients"]
    assert body["subject"].startswith("[TEST]")

    rows = session.scalars(
        select(AlertNotification).where(AlertNotification.alert_id == a.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].kind.value == "test"


# ---------------------------------------------------------------------------
# Alert recipients endpoint (admin-only)
# ---------------------------------------------------------------------------


def test_alerts_recipients_endpoint_admin_only(
    client, operator_with_access_to_two, restore_user_override
):
    _impersonate(operator_with_access_to_two)
    resp = client.get("/api/alerts/recipients")
    assert resp.status_code == 403


def test_alerts_recipients_endpoint_returns_per_warehouse_lists(client):
    resp = client.get("/api/alerts/recipients")
    assert resp.status_code == 200
    body = resp.json()
    assert "warehouses" in body
    assert "escalation" in body
    assert {w["warehouse_id"] for w in body["warehouses"]} == {1, 2, 3}


# ---------------------------------------------------------------------------
# Admin grant flow via PATCH /users/{id}
# ---------------------------------------------------------------------------


def test_admin_can_grant_warehouses_via_users_endpoint(
    client, operator_with_no_access, session
):
    resp = client.patch(
        f"/api/users/{operator_with_no_access.id}",
        json={"warehouse_ids": [1, 3]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["warehouse_ids"] == [1, 3]

    listing = client.get("/api/users").json()
    me = next(u for u in listing if u["id"] == operator_with_no_access.id)
    assert me["warehouse_ids"] == [1, 3]


def test_warehouse_ids_validates_existence(client, operator_with_no_access):
    resp = client.patch(
        f"/api/users/{operator_with_no_access.id}",
        json={"warehouse_ids": [1, 99999]},
    )
    assert resp.status_code == 400
    assert "99999" in resp.json()["detail"]


def test_admin_can_clear_warehouse_access(
    client, operator_with_access_to_two, session
):
    resp = client.patch(
        f"/api/users/{operator_with_access_to_two.id}",
        json={"warehouse_ids": []},
    )
    assert resp.status_code == 200
    assert resp.json()["warehouse_ids"] == []


# ---------------------------------------------------------------------------
# SSE payload filter (unit)
# ---------------------------------------------------------------------------


def test_sse_payload_filter_admin_passes_through():
    from app.routers.stream import _payload_visible_to

    assert _payload_visible_to(
        '{"type":"box.updated","data":{"warehouse_id":1}}', None
    )


def test_sse_payload_filter_respects_allowed_set():
    from app.routers.stream import _payload_visible_to

    allowed = {1, 2}
    assert _payload_visible_to(
        '{"type":"box.updated","data":{"warehouse_id":1}}', allowed
    )
    assert not _payload_visible_to(
        '{"type":"box.updated","data":{"warehouse_id":3}}', allowed
    )


def test_sse_payload_filter_fails_closed_on_missing_warehouse():
    """Non-admins should never receive an event we can't attribute to a
    warehouse -- otherwise a stray cross-warehouse signal would leak past
    the ACL."""
    from app.routers.stream import _payload_visible_to

    assert not _payload_visible_to(
        '{"type":"box.updated","data":{}}', {1, 2}
    )
    assert not _payload_visible_to("not-json", {1, 2})


# ---------------------------------------------------------------------------
# apply_warehouse_filter (unit)
# ---------------------------------------------------------------------------


def test_apply_warehouse_filter_admin_is_noop(session, make_user):
    from sqlalchemy import select

    from app.models.boxes import Box
    from app.models.users import UserRole
    from app.services.acl import apply_warehouse_filter

    admin = make_user(UserRole.admin)
    base = select(Box)
    filtered = apply_warehouse_filter(base, admin, Box.current_warehouse_id)
    assert str(filtered) == str(base)


def test_apply_warehouse_filter_empty_grant_returns_no_rows(
    client, operator_with_no_access, session
):
    """A non-admin with zero rows must see nothing -- the helper
    deliberately yields ``column.in_([])`` rather than passing the query
    through unmodified."""
    from sqlalchemy import select

    from app.models.boxes import Box
    from app.services.acl import apply_warehouse_filter

    client.post(
        "/api/boxes",
        json={"box_number": "F-1", "lot": "x", "warehouse_id": 1},
    )

    stmt = apply_warehouse_filter(
        select(Box), operator_with_no_access, Box.current_warehouse_id
    )
    rows = session.scalars(stmt).all()
    assert rows == []
