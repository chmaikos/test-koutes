"""Tests for the bulk-update and XLSX-import endpoints."""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook


def _create_box(client, *, box_number: str, warehouse_id: int = 1, lot: str = "x") -> int:
    resp = client.post(
        "/api/boxes",
        json={"box_number": box_number, "lot": lot, "warehouse_id": warehouse_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# Canonical forward chain; the helper walks intermediate states so tests
# only have to declare the milestones they care about.
_FORWARD_CHAIN = (
    "received",
    "processing",
    "incomplete",
    "ready_to_return",
    "returned",
)


def _advance(client, box_id: int, *statuses: str) -> None:
    """Walk a box forward through each requested status milestone."""
    for target in statuses:
        current = client.get(f"/api/boxes/{box_id}").json()["status"]
        start = _FORWARD_CHAIN.index(current)
        end = _FORWARD_CHAIN.index(target)
        for step in _FORWARD_CHAIN[start + 1 : end + 1]:
            resp = client.patch(f"/api/boxes/{box_id}", json={"status": step})
            assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# bulk update
# ---------------------------------------------------------------------------


def test_bulk_move_happy_path(client):
    ids = [_create_box(client, box_number=f"H-{i}") for i in range(3)]
    resp = client.post(
        "/api/boxes/bulk", json={"box_ids": ids, "warehouse_id": 2}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["updated"]) == 3
    assert body["skipped"] == []
    assert all(b["current_warehouse_id"] == 2 for b in body["updated"])


def test_bulk_move_mixed_skips_returned(client):
    keep_id = _create_box(client, box_number="MIX-1")
    move_again_id = _create_box(client, box_number="MIX-2")
    returned_id = _create_box(client, box_number="MIX-3")
    _advance(client, returned_id, "ready_to_return", "returned")

    resp = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [keep_id, move_again_id, returned_id], "warehouse_id": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    updated_ids = {b["id"] for b in body["updated"]}
    assert updated_ids == {keep_id, move_again_id}
    assert len(body["skipped"]) == 1
    skip = body["skipped"][0]
    assert skip["box_id"] == returned_id
    assert skip["box_number"] == "MIX-3"
    assert "returned" in skip["reason"].lower()


def test_bulk_status_change_mixed(client):
    a = _create_box(client, box_number="S-1")
    b = _create_box(client, box_number="S-2")
    done = _create_box(client, box_number="S-3")
    _advance(client, done, "ready_to_return", "returned")

    # Bulk-step the live boxes one position forward (received -> processing).
    # The third box is already ``returned`` and must be skipped because that
    # is a terminal state without ``force``.
    resp = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [a, b, done], "status": "processing"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    updated_ids = {b["id"] for b in body["updated"]}
    assert updated_ids == {a, b}
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["box_id"] == done


def test_bulk_rejects_empty_box_ids(client):
    resp = client.post("/api/boxes/bulk", json={"box_ids": [], "warehouse_id": 2})
    assert resp.status_code == 422


def test_bulk_rejects_no_change_specified(client):
    box_id = _create_box(client, box_number="NOOP-1")
    resp = client.post("/api/boxes/bulk", json={"box_ids": [box_id]})
    assert resp.status_code == 400
    assert "warehouse_id" in resp.json()["detail"]


def test_bulk_skips_missing_ids(client):
    real = _create_box(client, box_number="EXIST-1")
    resp = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [real, 999_999], "warehouse_id": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["updated"]) == 1
    assert body["updated"][0]["id"] == real
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["box_id"] == 999_999
    assert "not found" in body["skipped"][0]["reason"].lower()


# ---------------------------------------------------------------------------
# XLSX import
# ---------------------------------------------------------------------------


def _build_xlsx(rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _post_xlsx(client, content: bytes, *, warehouse_id: int | None = None):
    files = {
        "file": (
            "boxes.xlsx",
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    data = {}
    if warehouse_id is not None:
        data["warehouse_id"] = str(warehouse_id)
    return client.post("/api/boxes/import", files=files, data=data)


def test_import_happy_path(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "contents", "warehouse_id"],
            ["IMP-1", "Acme", "shoes", 1],
            ["IMP-2", "Globex", None, 2],
            ["IMP-3", "Initech", "spare parts", 3],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 3
    assert body["skipped"] == []
    by_number = {b["box_number"]: b for b in body["created"]}
    assert by_number["IMP-1"]["lot"] == "Acme"
    assert by_number["IMP-1"]["contents"] == "shoes"
    assert by_number["IMP-2"]["contents"] is None
    assert by_number["IMP-3"]["contents"] == "spare parts"


def test_import_resolves_warehouse_by_name(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse"],
            ["NAMED-1", "Acme", "Building 2"],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["current_warehouse_id"] == 2


def test_import_uses_default_warehouse(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot"],
            ["DEF-1", "Acme"],
            ["DEF-2", "Acme"],
        ]
    )
    resp = _post_xlsx(client, payload, warehouse_id=3)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 2
    assert all(b["current_warehouse_id"] == 3 for b in body["created"])


def test_import_duplicate_existing_box_is_skipped(client):
    _create_box(client, box_number="DUP-1", warehouse_id=1)

    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["NEW-1", "Acme", 1],
            ["DUP-1", "Acme", 1],
            ["NEW-2", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 2
    created_numbers = {b["box_number"] for b in body["created"]}
    assert created_numbers == {"NEW-1", "NEW-2"}
    assert len(body["skipped"]) == 1
    skip = body["skipped"][0]
    assert skip["box_number"] == "DUP-1"
    assert skip["row"] == 3  # header + two rows above
    assert "exists" in skip["reason"].lower()


def test_import_duplicate_within_file_is_skipped(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["TWIN-1", "Acme", 1],
            ["TWIN-1", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert len(body["skipped"]) == 1
    assert "duplicate" in body["skipped"][0]["reason"].lower()


def test_import_missing_box_number_cell(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["KEEP-1", "Acme", 1],
            [None, "Acme", 1],
            ["KEEP-2", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 2
    assert len(body["skipped"]) == 1
    skip = body["skipped"][0]
    assert skip["row"] == 3
    assert skip["box_number"] is None
    assert "empty" in skip["reason"].lower()


def test_import_missing_required_header(client):
    payload = _build_xlsx(
        [
            ["lot", "warehouse_id"],
            ["Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 400
    assert "box_number" in resp.json()["detail"]


def test_import_missing_lot_header_rejected(client):
    payload = _build_xlsx(
        [
            ["box_number", "warehouse_id"],
            ["NOLOT-1", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 400
    assert "lot" in resp.json()["detail"]


def test_import_skips_row_with_blank_lot(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["BLOT-1", "Acme", 1],
            ["BLOT-2", None, 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["box_number"] == "BLOT-1"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["box_number"] == "BLOT-2"
    assert "lot" in body["skipped"][0]["reason"].lower()


def test_import_unknown_warehouse_name_skipped(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse"],
            ["WHN-1", "Acme", "Atlantis"],
            ["WHN-2", "Acme", "Building 1"],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["box_number"] == "WHN-2"
    assert len(body["skipped"]) == 1
    assert "atlantis" in body["skipped"][0]["reason"].lower()


def test_import_rejects_non_xlsx_filename(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["BAD-1", "Acme", 1],
        ]
    )
    files = {
        "file": (
            "boxes.csv",
            payload,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    resp = client.post("/api/boxes/import", files=files)
    assert resp.status_code == 400
    assert "xlsx" in resp.json()["detail"].lower()
