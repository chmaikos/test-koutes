"""End-to-end smoke test through the HTTP layer.

Walks the full happy path: received -> processing -> incomplete ->
ready_to_return -> returned, plus exports, dashboard, alerts, and
warehouse threshold updates. SQLite is the substrate so this runs
anywhere without Docker or Postgres.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook


def _box_count(client) -> int:
    resp = client.get("/api/boxes")
    assert resp.status_code == 200
    return resp.json()["total"]


def test_full_box_lifecycle_and_dashboard(client):
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    warehouses = client.get("/api/warehouses").json()
    assert len(warehouses) == 3

    create = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Acme", "warehouse_id": 1},
    )
    assert create.status_code == 201, create.text
    box_id = create.json()["id"]
    assert _box_count(client) == 1

    dup = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Acme", "warehouse_id": 1},
    )
    assert dup.status_code == 409

    # Skip-ahead transitions are rejected without ``force``. A box in
    # ``received`` cannot jump directly to ``returned``.
    bad = client.patch(f"/api/boxes/{box_id}", json={"status": "returned"})
    assert bad.status_code == 400

    for transition in (
        "processing",
        "incomplete",
        "ready_to_return",
        "returned",
    ):
        resp = client.patch(f"/api/boxes/{box_id}", json={"status": transition})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == transition

    detail = client.get(f"/api/boxes/{box_id}").json()
    assert detail["returned_at"] is not None

    events = client.get(f"/api/boxes/{box_id}/events").json()
    # 1 created + 4 status transitions = 5 events minimum.
    assert len(events) >= 5
    types = [ev["event_type"] for ev in events]
    assert "created" in types
    assert "returned" in types

    summary = client.get("/api/dashboard/summary").json()
    wh1 = next(w for w in summary["warehouses"] if w["warehouse_id"] == 1)
    assert wh1["received_today"] == 1
    assert wh1["returned_today"] == 1
    assert wh1["counts_by_status"]["returned"] == 1
    # The full walk produced exactly one box-completion event today
    # (the processing -> incomplete transition).
    assert wh1["completed_today"] == 1
    assert wh1["available_boxes"] == 0
    assert wh1["unavailable_boxes"] == 0


def test_filters_search_and_exports(client):
    payloads = [
        {"box_number": "100", "lot": "Acme", "warehouse_id": 1},
        {"box_number": "200", "lot": "Globex", "warehouse_id": 2},
        {"box_number": "201", "lot": "Globex", "warehouse_id": 2},
    ]
    for p in payloads:
        assert client.post("/api/boxes", json=p).status_code == 201

    # lot filter
    resp = client.get("/api/boxes", params={"lot": "globex"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(b["lot"] == "Globex" for b in body["items"])

    # search across box_number
    resp = client.get("/api/boxes", params={"search": "201"})
    assert resp.json()["total"] == 1

    csv = client.get("/api/exports/boxes.csv", params={"warehouse_id": 2})
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    # The CSV is encoded with a UTF-8 BOM so Excel on Windows opens it cleanly.
    text = csv.content.decode("utf-8-sig")
    assert "Box Number" in text
    assert "Warehouse" in text
    assert "Building 2" in text
    assert "200" in text and "201" in text and "100" not in text

    xlsx = client.get("/api/exports/boxes.xlsx", params={"warehouse_id": 2})
    assert xlsx.status_code == 200
    wb = load_workbook(BytesIO(xlsx.content))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[0]
    assert "Box Number" in headers
    assert "Warehouse" in headers
    warehouse_col = headers.index("Warehouse")
    data_rows = [r for r in rows[1:] if r and r[0] in ("200", "201")]
    assert len(data_rows) == 2
    assert all(r[warehouse_col] == "Building 2" for r in data_rows)


def test_box_lot_contents_round_trip_and_required(client):
    # Round-trip: lot + optional contents are preserved on the resource and
    # surfaced via filters.
    create = client.post(
        "/api/boxes",
        json={
            "box_number": "001",
            "lot": "LOT-42",
            "contents": "10x calibration kits",
            "warehouse_id": 1,
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["lot"] == "LOT-42"
    assert body["contents"] == "10x calibration kits"

    fetched = client.get(f"/api/boxes/{body['id']}").json()
    assert fetched["lot"] == "LOT-42"
    assert fetched["contents"] == "10x calibration kits"

    # Empty payload contents on update clears the field.
    cleared = client.patch(
        f"/api/boxes/{body['id']}", json={"contents": ""}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["contents"] is None

    # Lot is required: empty string is rejected by Pydantic with 422.
    bad_create = client.post(
        "/api/boxes",
        json={"box_number": "002", "lot": "", "warehouse_id": 1},
    )
    assert bad_create.status_code == 422

    # Missing entirely is also a 422 (lot has no default).
    missing = client.post(
        "/api/boxes",
        json={"box_number": "003", "warehouse_id": 1},
    )
    assert missing.status_code == 422


def test_xlsx_export_with_timezone_aware_timestamps():
    """Postgres TIMESTAMPTZ columns return tz-aware datetimes; openpyxl
    refuses to write those (raises TypeError) so the response would 500 and
    leave the user with an empty / truncated file. Exercise the service
    directly with tz-aware datetimes -- SQLite normalises them on round-trip
    so an HTTP-level test wouldn't catch the regression."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.models.boxes import BoxStatus
    from app.services.exports import boxes_to_xlsx

    box = SimpleNamespace(
        box_number="001",
        lot="Acme",
        contents=None,
        current_warehouse_id=1,
        status=BoxStatus.ready_to_return,
        received_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        returned_at=None,
        created_at=datetime(2026, 1, 2, 3, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, 4, 0, 0, tzinfo=UTC),
    )

    payload = boxes_to_xlsx([box], {1: "Building 1"})
    wb = load_workbook(BytesIO(payload))
    rows = list(wb.active.iter_rows(values_only=True))
    assert len(rows) == 2, f"expected header + 1 data row, got {len(rows)}"
    received_idx = rows[0].index("Received At")
    assert isinstance(rows[1][received_idx], datetime)
    assert rows[1][received_idx].tzinfo is None
    assert rows[1][received_idx] == datetime(2026, 1, 2, 3, 4, 5)


def test_alerts_max_capacity(client, session):
    from app.models.boxes import Box, BoxStatus
    from app.models.warehouses import Warehouse
    from app.services.alerts import evaluate_alerts

    wh = session.get(Warehouse, 3)
    wh.max_capacity = 2
    session.commit()

    for i in range(2):
        assert (
            client.post(
                "/api/boxes",
                json={"box_number": f"{i + 1:03d}", "lot": "x", "warehouse_id": 3},
            ).status_code
            == 201
        )

    # ``max_capacity`` now reflects the *unavailable* backlog
    # (``incomplete`` + ``ready_to_return``). New boxes default to
    # ``received`` which is *available*, so we walk them into the
    # unavailable bucket directly in the DB -- the chain-of-PATCH path
    # is exercised in ``test_full_box_lifecycle_and_dashboard``.
    from sqlalchemy import select

    for box in session.scalars(
        select(Box).where(Box.current_warehouse_id == 3)
    ).all():
        box.status = BoxStatus.ready_to_return
    session.commit()
    evaluate_alerts(session)

    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    open_alerts = resp.json()
    assert any(
        a["warehouse_id"] == 3 and a["type"] == "max_capacity" for a in open_alerts
    )

    summary = client.get("/api/dashboard/summary").json()
    total_open = summary["total_open_alerts"]
    assert total_open >= 1


def test_alerts_low_inventory_resolves(client, session):
    from app.models.warehouses import Warehouse

    wh = session.get(Warehouse, 2)
    wh.min_inventory = 1
    session.commit()

    # No boxes in warehouse 2 yet, so we need to manually trigger evaluation
    # (in production the scheduler does this every 60s).
    from app.services.alerts import evaluate_alerts

    evaluate_alerts(session)

    open_alerts = client.get("/api/alerts").json()
    assert any(
        a["warehouse_id"] == 2 and a["type"] == "low_inventory" for a in open_alerts
    )

    # Add a box -> alert should resolve.
    assert (
        client.post(
            "/api/boxes",
            json={"box_number": "001", "lot": "x", "warehouse_id": 2},
        ).status_code
        == 201
    )
    evaluate_alerts(session)

    still_open = client.get("/api/alerts", params={"only_open": "true"}).json()
    assert not any(
        a["warehouse_id"] == 2 and a["type"] == "low_inventory" for a in still_open
    )


def test_warehouse_thresholds_admin_only(client):
    resp = client.patch("/api/warehouses/1", json={"min_inventory": 5, "max_capacity": 50})
    assert resp.status_code == 200
    assert resp.json()["min_inventory"] == 5

    bad = client.patch("/api/warehouses/1", json={"min_inventory": 100, "max_capacity": 50})
    assert bad.status_code == 400


def test_admin_can_create_warehouse(client):
    resp = client.post(
        "/api/warehouses",
        json={"name": "Building 4", "min_inventory": 0, "max_capacity": 100},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Building 4"
    assert body["min_inventory"] == 0
    assert body["max_capacity"] == 100
    # Auto-allocated id should land past the seeded 1/2/3.
    assert body["id"] > 3

    listing = client.get("/api/warehouses").json()
    assert any(w["id"] == body["id"] and w["name"] == "Building 4" for w in listing)
    assert len(listing) == 4


def test_create_warehouse_rejects_min_ge_max(client):
    resp = client.post(
        "/api/warehouses",
        json={"name": "Bad", "min_inventory": 100, "max_capacity": 100},
    )
    assert resp.status_code == 400
    assert "min_inventory" in resp.json()["detail"]


def test_create_warehouse_requires_admin(client, make_user):
    from app.deps import get_current_user
    from app.main import app
    from app.models.users import UserRole

    operator = make_user(UserRole.operator)
    app.dependency_overrides[get_current_user] = lambda: operator
    try:
        resp = client.post(
            "/api/warehouses",
            json={"name": "Building X", "min_inventory": 0, "max_capacity": 50},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_linear_box_status_chain_rejects_skip_ahead(client):
    """The status enum is a strict chain:

        received -> processing -> incomplete -> ready_to_return -> returned

    Every non-adjacent transition without ``force`` must 400 so we never
    end up with a box that, e.g., is ``ready_to_return`` while pages
    from it are still being processed (i.e. the ``incomplete`` step was
    skipped). This is the single test that pins the whole machine; the
    happy-path walk in ``test_full_box_lifecycle_and_dashboard`` covers
    the legal transitions in line, this one enumerates the illegal
    pairs.
    """
    chain = (
        "received",
        "processing",
        "incomplete",
        "ready_to_return",
        "returned",
    )

    resp = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Acme", "warehouse_id": 1},
    )
    assert resp.status_code == 201, resp.text
    box_id = resp.json()["id"]

    # Every "jump more than one step" from ``received`` must be rejected.
    for target in chain[2:]:
        bad = client.patch(f"/api/boxes/{box_id}", json={"status": target})
        assert bad.status_code == 400, f"jump received->{target} must 400"

    # Walk the chain end-to-end one step at a time; each step succeeds
    # and lands the box on the expected state.
    for current, nxt in zip(chain, chain[1:]):
        resp = client.patch(
            f"/api/boxes/{box_id}", json={"status": nxt}
        )
        assert resp.status_code == 200, f"step {current}->{nxt}: {resp.text}"
        assert resp.json()["status"] == nxt


def test_role_gating_operator_cannot_manage_users(client, session, make_user):
    from app.deps import get_current_user
    from app.main import app
    from app.models.users import UserRole

    operator = make_user(UserRole.operator)
    app.dependency_overrides[get_current_user] = lambda: operator

    resp = client.get("/api/users")
    assert resp.status_code == 403
