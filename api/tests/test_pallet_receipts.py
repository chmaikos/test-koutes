from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select

from app.models.boxes import Box, BoxStatus
from app.models.lots import Lot
from app.models.pallets import Pallet
from app.models.requests import (
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestOrigin,
)
from app.models.users import UserRole
from app.models.warehouses import ReceiptMode, Warehouse
from app.services.requests import create_completed_receipt


def _manual_payload(
    number: str,
    *,
    lot: str = "PALLET-RECEIPT",
    pallet: str = "PALLET-A",
    warehouse_id: int = 1,
    contents: str | None = None,
) -> dict[str, object]:
    return {
        "box_number": number,
        "lot": lot,
        "pallet_number": pallet,
        "warehouse_id": warehouse_id,
        "contents": contents,
    }


def test_manual_receipt_requires_resolves_and_reuses_pallet(client, session) -> None:
    missing = client.post(
        "/api/boxes",
        json={"box_number": "1", "lot": "PALLET-RECEIPT", "warehouse_id": 1},
    )
    assert missing.status_code == 422

    first = client.post("/api/boxes", json=_manual_payload("1"))
    second = client.post("/api/boxes", json=_manual_payload("2", pallet=" pallet-a "))
    assert first.status_code == second.status_code == 201
    assert first.json()["pallet_id"] == second.json()["pallet_id"]
    assert first.json()["pallet_number"] == "PALLET-A"
    assert len(session.scalars(select(Pallet)).all()) == 1
    receipt = client.get(f"/api/requests/{first.json()['receipt_request_id']}").json()
    assert receipt["items"][0]["pallet_id"] == first.json()["pallet_id"]
    assert receipt["items"][0]["pallet"] == "PALLET-A"
    sources = client.get(
        "/api/requests/return-sources", params={"warehouse_id": 1}
    ).json()
    source = next(item for item in sources if item["id"] == receipt["id"])
    assert source["pallets"] == [
        {"pallet_id": first.json()["pallet_id"], "pallet_number": "PALLET-A"}
    ]
    assert source["has_unassigned_boxes"] is False


def test_same_pallet_number_in_another_warehouse_is_conflict(client) -> None:
    assert client.post("/api/boxes", json=_manual_payload("1")).status_code == 201
    conflict = client.post(
        "/api/boxes",
        json=_manual_payload("2", warehouse_id=2),
    )
    assert conflict.status_code == 409
    assert "will not be moved" in conflict.json()["detail"]


def test_mapped_import_merges_contents_and_rejects_pallet_conflict(client) -> None:
    merged = client.post(
        "/api/boxes/import-mapped",
        json={
            "warehouse_id": 1,
            "items": [
                {
                    "box_number": "1",
                    "lot": "MAPPED-PALLET",
                    "pallet_number": "PALLET-M",
                    "contents": "Invoices",
                },
                {
                    "box_number": "001",
                    "lot": "mapped-pallet",
                    "pallet_number": " pallet-m ",
                    "contents": "Contracts",
                },
            ],
        },
    )
    assert merged.status_code == 200, merged.text
    assert len(merged.json()["created"]) == 1
    assert merged.json()["created"][0]["contents"] == "Invoices | Contracts"

    conflict = client.post(
        "/api/boxes/import-mapped",
        json={
            "warehouse_id": 1,
            "items": [
                {
                    "box_number": "2",
                    "lot": "MAPPED-PALLET",
                    "pallet_number": "PALLET-X",
                },
                {
                    "box_number": "002",
                    "lot": "MAPPED-PALLET",
                    "pallet_number": "PALLET-Y",
                },
            ],
        },
    )
    assert conflict.status_code == 400
    assert "different pallet values" in conflict.json()["detail"]


def test_raw_xlsx_import_requires_pallet_column(client) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["box_number", "lot", "warehouse_id"])
    sheet.append(["1", "RAW-PALLET", 1])
    payload = BytesIO()
    workbook.save(payload)
    response = client.post(
        "/api/boxes/import",
        files={
            "file": (
                "missing-pallet.xlsx",
                payload.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 400
    assert "missing required column 'pallet_number'" in response.json()["detail"]


def test_staged_snapshot_survives_pallet_rename_and_finalizes(client, session) -> None:
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.receipt_mode = ReceiptMode.admin_review
    session.commit()

    staged = client.post(
        "/api/boxes",
        json=_manual_payload("10", lot="STAGED-PALLET", pallet="PALLET-BEFORE"),
    )
    request_id = staged.json()["staged_receipt_id"]
    request = client.get(f"/api/requests/{request_id}").json()
    item = request["items"][0]
    pallet = client.get(f"/api/pallets/{item['pallet_id']}").json()
    renamed = client.patch(
        f"/api/pallets/{item['pallet_id']}/rename",
        json={
            "new_pallet_number": "PALLET-AFTER",
            "reason": "Correct label",
            "expected_version": pallet["version"],
        },
    )
    assert renamed.status_code == 200, renamed.text

    finalized = client.post(
        f"/api/requests/{request_id}/approve",
        json={"expected_version": request["version"]},
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["items"][0]["pallet"] == "PALLET-BEFORE"
    box = session.get(Box, finalized.json()["items"][0]["box_id"])
    assert box is not None
    assert box.pallet_id == item["pallet_id"]
    assert box.pallet.pallet_number == "PALLET-AFTER"


def test_legacy_unassigned_box_remains_returnable_and_snapshots_null(
    client, session, make_user
) -> None:
    operator = make_user(UserRole.operator)
    lot = Lot(name="LEGACY-RETURN")
    session.add(lot)
    session.flush()
    box = Box(
        box_number="001",
        lot_id=lot.id,
        current_warehouse_id=1,
        status=BoxStatus.ready_to_return,
        received_at=datetime.now(UTC),
    )
    session.add(box)
    session.flush()
    source = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[box],
        origin=BoxRequestOrigin.manual_entry,
        note="Legacy receipt compatibility.",
    )
    assert source.items[0].pallet_id is None
    assert source.items[0].pallet is None
    sources = client.get(
        "/api/requests/return-sources", params={"warehouse_id": 1}
    ).json()
    legacy_source = next(item for item in sources if item["id"] == source.id)
    assert legacy_source["pallets"] == []
    assert legacy_source["has_unassigned_boxes"] is True

    candidates = client.get(f"/api/requests/{source.id}/return-candidates")
    assert candidates.status_code == 200
    assert candidates.json()[0]["pallet_id"] is None
    assert candidates.json()[0]["pallet_number"] is None
    created = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "target_warehouse_id": 2,
            "quantity": 1,
            "source_inbound_request_id": source.id,
            "box_ids": [box.id],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["items"][0]["pallet_id"] is None
    assert created.json()["items"][0]["pallet"] is None


def test_request_reporting_search_export_and_long_contents_include_pallet(
    client, session
) -> None:
    contents = "x" * 1500
    created = client.post(
        "/api/boxes",
        json=_manual_payload(
            "20",
            lot="REPORT-PALLET",
            pallet="SEARCHABLE-PALLET",
            contents=contents,
        ),
    )
    assert created.status_code == 201, created.text
    assert created.json()["contents"] == contents
    assert client.get("/api/boxes", params={"search": "searchable-pallet"}).json()[
        "total"
    ] == 1
    requests = client.get(
        "/api/requests", params={"search": "searchable-pallet"}
    ).json()
    assert requests["total"] == 1
    assert requests["items"][0]["items"][0]["pallet_number"] == "SEARCHABLE-PALLET"
    exported = client.get("/api/exports/boxes.csv")
    assert exported.status_code == 200
    header = exported.content.decode("utf-8-sig").splitlines()[0]
    assert "Pallet ID" in header and "Pallet Number" in header

    receipt_id = created.json()["receipt_request_id"]
    session.add(
        BoxRequestEvent(
            request_id=receipt_id,
            event_type=BoxRequestEventType.cancelled,
            user_id=None,
            note="Reporting context",
            event_metadata={
                "admin_override": True,
                "cancelled_reservation": True,
                "box_id": created.json()["id"],
            },
        )
    )
    session.commit()
    report = client.get("/api/requests/reconciliation").json()
    issue = next(
        item
        for item in report["items"]
        if item["request_id"] == receipt_id
        and item["issue_type"] == "cancelled_reservation"
    )
    assert issue["pallet_id"] == created.json()["pallet_id"]
    assert issue["pallet_number"] == "SEARCHABLE-PALLET"
