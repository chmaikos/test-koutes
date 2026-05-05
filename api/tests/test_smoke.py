"""End-to-end smoke test through the HTTP layer.

Walks the full happy path: receive -> in_progress -> processing_complete ->
ready_to_return -> returned, plus exports, dashboard, alerts, and warehouse
threshold updates. SQLite is the substrate so this runs anywhere without
Docker or Postgres.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select


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
        json={"box_number": "B-001", "owner": "Acme", "warehouse_id": 1},
    )
    assert create.status_code == 201, create.text
    box_id = create.json()["id"]
    assert _box_count(client) == 1

    dup = client.post(
        "/api/boxes",
        json={"box_number": "B-001", "owner": "Acme", "warehouse_id": 1},
    )
    assert dup.status_code == 409

    bad = client.patch(f"/api/boxes/{box_id}", json={"status": "returned"})
    assert bad.status_code == 400

    for transition in ("in_progress", "processing_complete", "ready_to_return", "returned"):
        resp = client.patch(f"/api/boxes/{box_id}", json={"status": transition})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == transition

    detail = client.get(f"/api/boxes/{box_id}").json()
    assert detail["returned_at"] is not None
    assert detail["processing_completed_at"] is not None

    events = client.get(f"/api/boxes/{box_id}/events").json()
    assert len(events) >= 5
    types = [ev["event_type"] for ev in events]
    assert "created" in types
    assert "returned" in types

    summary = client.get("/api/dashboard/summary").json()
    wh1 = next(w for w in summary["warehouses"] if w["warehouse_id"] == 1)
    assert wh1["received_today"] == 1
    assert wh1["returned_today"] == 1
    assert wh1["counts_by_status"]["returned"] == 1


def test_filters_search_and_exports(client):
    payloads = [
        {"box_number": "B-100", "owner": "Acme", "warehouse_id": 1},
        {"box_number": "B-200", "owner": "Globex", "warehouse_id": 2},
        {"box_number": "B-201", "owner": "Globex", "warehouse_id": 2},
    ]
    for p in payloads:
        assert client.post("/api/boxes", json=p).status_code == 201

    # owner filter
    resp = client.get("/api/boxes", params={"owner": "globex"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(b["owner"] == "Globex" for b in body["items"])

    # search across box_number
    resp = client.get("/api/boxes", params={"search": "B-201"})
    assert resp.json()["total"] == 1

    csv = client.get("/api/exports/boxes.csv", params={"warehouse_id": 2})
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    # The CSV is encoded with a UTF-8 BOM so Excel on Windows opens it cleanly.
    text = csv.content.decode("utf-8-sig")
    assert "Box Number" in text
    assert "Warehouse" in text
    assert "Building 2" in text
    assert "B-200" in text and "B-201" in text and "B-100" not in text

    xlsx = client.get("/api/exports/boxes.xlsx", params={"warehouse_id": 2})
    assert xlsx.status_code == 200
    wb = load_workbook(BytesIO(xlsx.content))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[0]
    assert "Box Number" in headers
    assert "Warehouse" in headers
    warehouse_col = headers.index("Warehouse")
    data_rows = [r for r in rows[1:] if r and r[0] in ("B-200", "B-201")]
    assert len(data_rows) == 2
    assert all(r[warehouse_col] == "Building 2" for r in data_rows)


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
        box_number="TZ-1",
        owner="Acme",
        current_warehouse_id=1,
        status=BoxStatus.processing_complete,
        received_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        processing_completed_at=datetime(2026, 1, 2, 4, 0, 0, tzinfo=UTC),
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
    from app.models.warehouses import Warehouse

    wh = session.get(Warehouse, 3)
    wh.max_capacity = 2
    session.commit()

    for i in range(2):
        assert (
            client.post(
                "/api/boxes",
                json={"box_number": f"M-{i}", "owner": "x", "warehouse_id": 3},
            ).status_code
            == 201
        )

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
            json={"box_number": "L-1", "owner": "x", "warehouse_id": 2},
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


def test_role_gating_operator_cannot_manage_users(client, session, make_user):
    from app.deps import get_current_user
    from app.main import app
    from app.models.users import UserRole

    operator = make_user(UserRole.operator)
    app.dependency_overrides[get_current_user] = lambda: operator

    resp = client.get("/api/users")
    assert resp.status_code == 403
