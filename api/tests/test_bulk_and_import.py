"""Tests for the bulk-update and XLSX-import endpoints."""
from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from app.models.boxes import Box


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


def test_bulk_move_linked_boxes_requires_audited_override(client):
    ids = [_create_box(client, box_number=f"{i + 1:03d}") for i in range(3)]
    resp = client.post(
        "/api/boxes/bulk",
        json={
            "box_ids": ids,
            "warehouse_id": 2,
            "force": True,
            "note": "Correcting receipt warehouse",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["updated"]) == 3
    assert body["skipped"] == []
    assert all(b["current_warehouse_id"] == 2 for b in body["updated"])


def test_bulk_move_without_override_skips_request_linked_boxes(client):
    keep_id = _create_box(client, box_number="001")
    move_again_id = _create_box(client, box_number="002")
    returned_id = _create_box(client, box_number="003")
    _advance(client, returned_id, "ready_to_return", "returned")

    resp = client.post(
        "/api/boxes/bulk",
        json={"box_ids": [keep_id, move_again_id, returned_id], "warehouse_id": 2},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == []
    assert {skip["box_id"] for skip in body["skipped"]} == {
        keep_id,
        move_again_id,
        returned_id,
    }
    assert all("linked to request" in skip["reason"] for skip in body["skipped"])


def test_bulk_status_change_mixed(client):
    a = _create_box(client, box_number="001")
    b = _create_box(client, box_number="002")
    done = _create_box(client, box_number="003")
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
    box_id = _create_box(client, box_number="001")
    resp = client.post("/api/boxes/bulk", json={"box_ids": [box_id]})
    assert resp.status_code == 400
    assert "warehouse_id" in resp.json()["detail"]


def test_bulk_skips_missing_ids(client):
    real = _create_box(client, box_number="001")
    resp = client.post(
        "/api/boxes/bulk",
        json={
            "box_ids": [real, 999_999],
            "warehouse_id": 2,
            "force": True,
            "note": "Correcting receipt warehouse",
        },
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
            ["001", "Acme", "shoes", 1],
            ["002", "Globex", None, 2],
            ["003", "Initech", "spare parts", 3],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 3
    assert body["skipped"] == []
    by_number = {b["box_number"]: b for b in body["created"]}
    assert by_number["001"]["lot"] == "Acme"
    assert by_number["001"]["contents"] == "shoes"
    assert by_number["002"]["contents"] is None
    assert by_number["003"]["contents"] == "spare parts"
    assert len(body["receipt_request_ids"]) == 3
    receipts = [
        client.get(f"/api/requests/{request_id}").json()
        for request_id in body["receipt_request_ids"]
    ]
    assert all(receipt["origin"] == "xlsx_import" for receipt in receipts)
    assert all(receipt["status"] == "completed" for receipt in receipts)
    assert sum(receipt["quantity"] for receipt in receipts) == 3


def test_box_import_mapper_previews_arbitrary_layout_and_imports_selection(client):
    payload = _build_xlsx(
        [
            ["ERP export", None, None],
            ["Description", "Container", "Batch"],
            ["Invoices", "7", "LOT-MAPPED"],
            ["Contracts", "8", "LOT-MAPPED"],
        ]
    )
    preview = client.post(
        "/api/boxes/import-preview",
        files={
            "file": (
                "arbitrary.xlsx",
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["sheets"][0]["rows"][0]["cells"][0] == "ERP export"

    imported = client.post(
        "/api/boxes/import-mapped",
        json={
            "warehouse_id": 2,
            "items": [
                {
                    "box_number": "7",
                    "lot": "LOT-MAPPED",
                    "contents": "Invoices",
                },
                {
                    "box_number": "8",
                    "lot": "LOT-MAPPED",
                    "contents": "Contracts",
                },
            ],
        },
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert [box["box_number"] for box in body["created"]] == ["007", "008"]
    assert all(box["current_warehouse_id"] == 2 for box in body["created"])
    assert len(body["receipt_request_ids"]) == 1
    receipt = client.get(
        f"/api/requests/{body['receipt_request_ids'][0]}"
    ).json()
    assert receipt["origin"] == "xlsx_import"
    assert receipt["quantity"] == 2


def test_mapped_import_can_explicitly_restore_archived_box(client):
    original = client.post(
        "/api/boxes",
        json={
            "box_number": "9",
            "lot": "RESTORE-ME",
            "contents": "Wrong contents",
            "warehouse_id": 1,
        },
    ).json()
    original_receipt_id = original["receipt_request_id"]
    _advance(client, original["id"], "returned")
    archived = client.post(
        f"/api/boxes/{original['id']}/delete",
        json={"force": True, "reason": "Imported by mistake"},
    )
    assert archived.status_code == 200

    payload = {
        "warehouse_id": 2,
        "items": [
            {
                "box_number": "9",
                "lot": "RESTORE-ME",
                "contents": "Correct contents",
            }
        ],
    }
    blocked = client.post("/api/boxes/import-mapped", json=payload)
    assert blocked.status_code == 200
    assert blocked.json()["created"] == []
    assert blocked.json()["restored"] == []
    assert "already exists" in blocked.json()["skipped"][0]["reason"]

    restored = client.post(
        "/api/boxes/import-mapped",
        json={**payload, "restore_archived": True},
    )
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["created"] == []
    assert [box["id"] for box in body["restored"]] == [original["id"]]
    restored_box = body["restored"][0]
    assert restored_box["archived_at"] is None
    assert restored_box["archive_reason"] is None
    assert restored_box["status"] == "received"
    assert restored_box["current_warehouse_id"] == 2
    assert restored_box["contents"] == "Correct contents"
    assert restored_box["returned_at"] is None

    new_receipt = client.get(
        f"/api/requests/{body['receipt_request_ids'][0]}"
    ).json()
    old_receipt = client.get(f"/api/requests/{original_receipt_id}").json()
    assert new_receipt["items"][0]["box_id"] == original["id"]
    assert old_receipt["items"][0]["box_id"] == original["id"]
    events = client.get(f"/api/boxes/{original['id']}/events").json()
    assert events[0]["event_type"] == "restored"
    assert "Imported by mistake" in events[0]["note"]

    active_duplicate = client.post(
        "/api/boxes/import-mapped",
        json={**payload, "restore_archived": True},
    ).json()
    assert active_duplicate["restored"] == []
    assert "already exists" in active_duplicate["skipped"][0]["reason"]


def test_legacy_xlsx_import_can_restore_archived_box(client):
    original = client.post(
        "/api/boxes",
        json={"box_number": "15", "lot": "LEGACY-RESTORE", "warehouse_id": 1},
    ).json()
    client.post(
        f"/api/boxes/{original['id']}/delete",
        json={"force": True, "reason": "Incorrect legacy import"},
    )
    workbook = _build_xlsx(
        [
            ["box_number", "lot", "contents", "warehouse_id"],
            ["15", "LEGACY-RESTORE", "Corrected", 1],
        ]
    )
    response = client.post(
        "/api/boxes/import",
        files={
            "file": (
                "legacy.xlsx",
                workbook,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"restore_archived": "true"},
    )
    assert response.status_code == 200, response.text
    assert [box["id"] for box in response.json()["restored"]] == [original["id"]]


def test_archived_restore_rolls_back_when_receipt_creation_fails(
    client, session, monkeypatch
):
    original = client.post(
        "/api/boxes",
        json={"box_number": "20", "lot": "ROLLBACK", "warehouse_id": 1},
    ).json()
    client.post(
        f"/api/boxes/{original['id']}/delete",
        json={"force": True, "reason": "Rollback test"},
    )

    def fail_receipt(*_args, **_kwargs):
        raise RuntimeError("receipt creation failed")

    monkeypatch.setattr(
        "app.services.imports.create_completed_receipt",
        fail_receipt,
    )
    with pytest.raises(RuntimeError, match="receipt creation failed"):
        client.post(
            "/api/boxes/import-mapped",
            json={
                "warehouse_id": 2,
                "restore_archived": True,
                "items": [{"box_number": "20", "lot": "ROLLBACK"}],
            },
        )
    session.rollback()
    session.expire_all()
    box = session.get(Box, original["id"])
    assert box.archived_at is not None
    assert box.current_warehouse_id == 1


def test_manual_box_creates_completed_receipt(client):
    response = client.post(
        "/api/boxes",
        json={"box_number": "1", "lot": "MANUAL", "warehouse_id": 1},
    )
    assert response.status_code == 201
    receipt_id = response.json()["receipt_request_id"]
    assert receipt_id is not None
    receipt = client.get(f"/api/requests/{receipt_id}").json()
    assert receipt["origin"] == "manual_entry"
    assert receipt["status"] == "completed"
    assert receipt["actual_received_quantity"] == 1
    assert receipt["items"][0]["box_id"] == response.json()["id"]


def test_imported_boxes_are_available_to_linked_return_flow(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["1", "IMPORT-RETURN", 1],
            ["2", "IMPORT-RETURN", 1],
        ]
    )
    imported = _post_xlsx(client, payload).json()
    assert len(imported["receipt_request_ids"]) == 1
    source_id = imported["receipt_request_ids"][0]
    for box in imported["created"]:
        _advance(client, box["id"], "ready_to_return")

    candidates = client.get(f"/api/requests/{source_id}/return-candidates")
    assert {item["box_id"] for item in candidates.json()} == {
        box["id"] for box in imported["created"]
    }
    created_return = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "target_warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [imported["created"][0]["id"]],
        },
    )
    assert created_return.status_code == 201


def test_import_resolves_warehouse_by_name(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse"],
            ["001", "Acme", "Building 2"],
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
            ["001", "Acme"],
            ["002", "Acme"],
        ]
    )
    resp = _post_xlsx(client, payload, warehouse_id=3)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 2
    assert all(b["current_warehouse_id"] == 3 for b in body["created"])


def test_import_duplicate_existing_box_is_skipped(client):
    # The seed box and the duplicate row share BOTH ``lot`` and
    # ``box_number``; only that combination is a conflict now that
    # uniqueness is scoped per-lot.
    _create_box(client, box_number="010", lot="Acme", warehouse_id=1)

    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["001", "Acme", 1],
            ["010", "Acme", 1],
            ["002", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 2
    created_numbers = {b["box_number"] for b in body["created"]}
    assert created_numbers == {"001", "002"}
    assert len(body["skipped"]) == 1
    skip = body["skipped"][0]
    assert skip["box_number"] == "010"
    assert skip["row"] == 3  # header + two rows above
    assert "exists" in skip["reason"].lower()


def test_import_duplicate_within_file_is_skipped(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["001", "Acme", 1],
            ["001", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert len(body["skipped"]) == 1
    # The dedupe key is the ``(lot, box_number)`` pair now.
    reason = body["skipped"][0]["reason"].lower()
    assert "duplicate" in reason
    assert "lot" in reason


def test_import_missing_box_number_cell(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["001", "Acme", 1],
            [None, "Acme", 1],
            ["002", "Acme", 1],
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
            ["001", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 400
    assert "lot" in resp.json()["detail"]


def test_import_skips_row_with_blank_lot(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["001", "Acme", 1],
            ["002", None, 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["box_number"] == "001"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["box_number"] == "002"
    assert "lot" in body["skipped"][0]["reason"].lower()


def test_import_unknown_warehouse_name_skipped(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse"],
            ["001", "Acme", "Atlantis"],
            ["002", "Acme", "Building 1"],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["box_number"] == "002"
    assert len(body["skipped"]) == 1
    assert "atlantis" in body["skipped"][0]["reason"].lower()


def test_import_rejects_non_xlsx_filename(client):
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["001", "Acme", 1],
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


# ---------------------------------------------------------------------------
# box_number rules (numeric-only, zero-padded to 3 digits, per-lot uniqueness)
# ---------------------------------------------------------------------------


def test_create_pads_box_number_to_three_digits(client):
    """``"1"`` is canonicalised to ``"001"`` at the schema layer so the
    persisted row always uses the padded form regardless of what the
    operator typed in the form."""
    resp = client.post(
        "/api/boxes",
        json={"box_number": "1", "lot": "Acme", "warehouse_id": 1},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["box_number"] == "001"

    bigger = client.post(
        "/api/boxes",
        json={"box_number": "42", "lot": "Acme", "warehouse_id": 1},
    )
    assert bigger.json()["box_number"] == "042"

    # Already wider than 3 chars: passes through unchanged.
    wide = client.post(
        "/api/boxes",
        json={"box_number": "1234", "lot": "Acme", "warehouse_id": 1},
    )
    assert wide.json()["box_number"] == "1234"


def test_create_rejects_non_numeric_box_number(client):
    """Non-digit characters in ``box_number`` are a 422 at the schema."""
    resp = client.post(
        "/api/boxes",
        json={"box_number": "abc", "lot": "Acme", "warehouse_id": 1},
    )
    assert resp.status_code == 422

    mixed = client.post(
        "/api/boxes",
        json={"box_number": "12X", "lot": "Acme", "warehouse_id": 1},
    )
    assert mixed.status_code == 422


def test_same_box_number_allowed_across_lots(client):
    """The uniqueness key is ``(lot, box_number)``; the same number in
    a different lot is a brand-new box, not a conflict."""
    first = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Acme", "warehouse_id": 1},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Globex", "warehouse_id": 1},
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]


def test_duplicate_within_same_lot_is_a_conflict(client):
    """Same ``(lot, box_number)`` pair still 409s."""
    first = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Acme", "warehouse_id": 1},
    )
    assert first.status_code == 201, first.text

    dup = client.post(
        "/api/boxes",
        json={"box_number": "001", "lot": "Acme", "warehouse_id": 1},
    )
    assert dup.status_code == 409
    assert "Acme" in dup.json()["detail"]


def test_import_pads_numeric_box_numbers(client):
    """XLSX import shares the same canonicalisation as the API."""
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            [1, "Acme", 1],   # openpyxl returns this as float 1.0 -> "1"
            ["42", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    numbers = {b["box_number"] for b in body["created"]}
    assert numbers == {"001", "042"}


def test_import_rejects_non_numeric_rows(client):
    """Non-numeric ``box_number`` cells are surfaced per-row, not 422
    on the whole upload."""
    payload = _build_xlsx(
        [
            ["box_number", "lot", "warehouse_id"],
            ["001", "Acme", 1],
            ["abc", "Acme", 1],
        ]
    )
    resp = _post_xlsx(client, payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["box_number"] == "001"
    assert len(body["skipped"]) == 1
    skip = body["skipped"][0]
    assert skip["box_number"] == "abc"
    assert "numeric" in skip["reason"].lower()
