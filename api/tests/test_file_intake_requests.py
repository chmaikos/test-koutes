from __future__ import annotations

import io

from openpyxl import Workbook
from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models import (
    Box,
    BoxFile,
    BoxFileEvent,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
    BoxRequestItemFileSnapshot,
    BoxRequestStatus,
    BoxStatus,
    Lot,
)
from app.models.users import UserRole
from app.models.warehouses import ReceiptMode, Warehouse


def test_manual_receipt_structured_files_override_legacy_and_snapshot(
    client, session
) -> None:
    response = client.post(
        "/api/boxes",
        json={
            "box_number": "81",
            "lot": "Structured receipt",
            "warehouse_id": 1,
            "contents": "must remain historical only",
            "files": [
                {
                    "reference": " FILE-2 ",
                    "description": "Second",
                    "barcode": "BC-2",
                },
                {"reference": "FILE-1", "description": "First"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["active_file_count"] == 2
    assert [file["reference"] for file in payload["files"]] == ["FILE-2", "FILE-1"]
    assert all(not file["reference"].startswith("LEGACY-") for file in payload["files"])

    snapshots = session.scalars(select(BoxRequestItemFileSnapshot)).all()
    assert {(item.reference, item.description) for item in snapshots} == {
        ("FILE-1", "First"),
        ("FILE-2", "Second"),
    }
    assert all(item.file_id is not None for item in snapshots)
    assert session.scalar(select(BoxFileEvent)) is not None


def test_manual_legacy_contents_split_and_staged_snapshot_link(
    client, session
) -> None:
    warehouse = session.get(Warehouse, 1)
    warehouse.receipt_mode = ReceiptMode.admin_review
    session.commit()
    staged = client.post(
        "/api/boxes",
        json={
            "box_number": "82",
            "lot": "Staged legacy",
            "warehouse_id": 1,
            "contents": " Alpha | | Beta ",
        },
    )
    assert staged.status_code == 201, staged.text
    request = session.get(BoxRequest, staged.json()["staged_receipt_id"])
    assert request is not None
    snapshots = request.items[0].file_snapshots
    assert [snapshot.description for snapshot in snapshots] == ["Alpha", "Beta"]
    assert all(snapshot.file_id is None for snapshot in snapshots)

    approved = client.post(
        f"/api/requests/{request.id}/approve",
        json={"expected_version": request.version},
    )
    assert approved.status_code == 200, approved.text
    session.expire_all()
    request = session.get(BoxRequest, request.id)
    assert request is not None
    assert all(snapshot.file_id is not None for snapshot in request.items[0].file_snapshots)
    box = session.get(Box, request.items[0].box_id)
    assert box is not None
    assert [file.reference for file in box.active_files] == [
        "LEGACY-BOX-082-FILE-1",
        "LEGACY-BOX-082-FILE-2",
    ]


def test_mapped_import_groups_file_rows_and_rejects_duplicate_targets(
    client, session
) -> None:
    imported = client.post(
        "/api/boxes/import-mapped",
        json={
            "warehouse_id": 1,
            "items": [
                {
                    "box_number": "91",
                    "lot": "Mapped files",
                    "files": [{"reference": "F-1", "description": "One"}],
                },
                {
                    "box_number": "091",
                    "lot": "mapped files",
                    "files": [{"reference": "F-2", "barcode": "BC-2"}],
                },
            ],
        },
    )
    assert imported.status_code == 200, imported.text
    assert len(imported.json()["created"]) == 1
    assert {
        file["reference"] for file in imported.json()["created"][0]["files"]
    } == {"F-1", "F-2"}

    conflict = client.post(
        "/api/boxes/import-mapped",
        json={
            "warehouse_id": 1,
            "items": [
                {
                    "box_number": "92",
                    "lot": "Conflicting files",
                    "files": [{"reference": "SAME"}],
                },
                {
                    "box_number": "93",
                    "lot": "Conflicting files",
                    "files": [{"reference": "same"}],
                },
            ],
        },
    )
    assert conflict.status_code == 400
    assert "targets two boxes" in conflict.json()["detail"]


def test_direct_xlsx_import_maps_first_class_file_columns(client) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "box_number",
            "lot",
            "warehouse_id",
            "file_reference",
            "file_description",
            "barcode",
        ]
    )
    sheet.append(["94", "Direct files", 1, "D-1", "First", "BC-1"])
    sheet.append(["094", "direct files", 1, "D-2", "Second", None])
    payload = io.BytesIO()
    workbook.save(payload)

    response = client.post(
        "/api/boxes/import",
        files={
            "file": (
                "files.xlsx",
                payload.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 1
    assert {
        (file["reference"], file["description"], file["barcode"])
        for file in response.json()["created"][0]["files"]
    } == {
        ("D-1", "First", "BC-1"),
        ("D-2", "Second", None),
    }


def test_return_request_snapshots_current_files_and_keeps_them_immutable(
    client, session
) -> None:
    receipt = client.post(
        "/api/boxes",
        json={
            "box_number": "95",
            "lot": "Return files",
            "warehouse_id": 1,
            "files": [{"reference": "RETURN-1", "description": "At selection"}],
        },
    )
    assert receipt.status_code == 201, receipt.text
    box = session.get(Box, receipt.json()["id"])
    assert box is not None
    box.status = BoxStatus.ready_to_return
    session.commit()

    candidate = client.get(
        f"/api/requests/{receipt.json()['receipt_request_id']}/return-candidates"
    )
    assert candidate.status_code == 200, candidate.text
    assert candidate.json()[0]["file_count"] == 1
    assert candidate.json()[0]["file_summary"] == "RETURN-1"

    response = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "target_warehouse_id": 2,
            "quantity": 1,
            "source_inbound_request_id": receipt.json()["receipt_request_id"],
            "box_ids": [box.id],
        },
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()["items"][0]["files"][0]
    assert snapshot["reference"] == "RETURN-1"
    assert snapshot["description"] == "At selection"

    file = session.scalar(
        select(BoxFile).where(BoxFile.normalized_reference == "return-1")
    )
    assert file is not None
    updated = client.patch(
        f"/api/files/{file.id}",
        json={
            "expected_version": file.version,
            "description": "Changed after selection",
        },
    )
    assert updated.status_code == 200
    detail = client.get(f"/api/requests/{response.json()['id']}")
    assert detail.json()["items"][0]["files"][0]["description"] == "At selection"


def test_inbound_file_reconciliation_move_ack_and_stale_signature(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = Lot(name="Reconcile files")
    session.add(lot)
    session.flush()
    target = Box(
        lot_id=lot.id,
        box_number="001",
        current_warehouse_id=2,
        status=BoxStatus.received,
    )
    source = Box(
        lot_id=lot.id,
        box_number="002",
        current_warehouse_id=2,
        status=BoxStatus.received,
    )
    session.add_all([target, source])
    session.flush()
    session.add_all(
        [
            BoxFile(
                lot_id=lot.id,
                box_id=target.id,
                reference="UPDATE",
                description="Before",
                position=1,
            ),
            BoxFile(
                lot_id=lot.id,
                box_id=target.id,
                reference="PRESERVE",
                position=2,
            ),
            BoxFile(
                lot_id=lot.id,
                box_id=source.id,
                reference="MOVE",
                position=1,
            ),
        ]
    )
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.awaiting_confirmation,
        requester_user_id=requester.id,
    )
    session.add(request)
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: requester
    inbound_items = [
        {
            "lot": lot.name,
            "box_number": target.box_number,
            "files": [
                {"reference": "UPDATE", "description": "After"},
                {"reference": "MOVE"},
                {"reference": "CREATE"},
            ],
        }
    ]
    preview = client.post(
        f"/api/requests/{request.id}/inbound-completion-preview",
        json={"inbound_items": inbound_items},
    )
    assert preview.status_code == 200, preview.text
    summary = preview.json()["summary"]
    assert summary["files_created"] == 1
    assert summary["files_updated"] == 1
    assert summary["files_moved"] == 1
    assert summary["files_preserved"] == 1

    missing_ack = client.post(
        f"/api/requests/{request.id}/complete",
        json={
            "expected_version": request.version,
            "idempotency_key": "missing-ack",
            "inbound_items": inbound_items,
            "accept_existing_received_boxes": True,
            "inbound_impact_signature": preview.json()["impact_signature"],
        },
    )
    assert missing_ack.status_code == 409
    assert "accept_file_moves" in missing_ack.json()["detail"]["message"]

    live = session.scalar(
        select(BoxFile).where(BoxFile.normalized_reference == "update")
    )
    assert live is not None
    live.description = "Concurrent change"
    session.commit()
    stale = client.post(
        f"/api/requests/{request.id}/complete",
        json={
            "expected_version": request.version,
            "idempotency_key": "stale-file-state",
            "inbound_items": inbound_items,
            "accept_existing_received_boxes": True,
            "accept_file_moves": True,
            "inbound_impact_signature": preview.json()["impact_signature"],
        },
    )
    assert stale.status_code == 409
    assert "impact changed" in stale.json()["detail"]["message"]

    refreshed = client.post(
        f"/api/requests/{request.id}/inbound-completion-preview",
        json={"inbound_items": inbound_items},
    )
    completed = client.post(
        f"/api/requests/{request.id}/complete",
        json={
            "expected_version": request.version,
            "idempotency_key": "accepted-file-moves",
            "inbound_items": inbound_items,
            "accept_existing_received_boxes": True,
            "accept_file_moves": True,
            "inbound_impact_signature": refreshed.json()["impact_signature"],
        },
    )
    assert completed.status_code == 200, completed.text
    session.expire_all()
    assert {
        file.reference
        for file in session.scalars(
            select(BoxFile).where(
                BoxFile.box_id == target.id,
                BoxFile.archived_at.is_(None),
            )
        ).all()
    } == {"UPDATE", "PRESERVE", "MOVE", "CREATE"}
    item_files = completed.json()["items"][0]["files"]
    assert {snapshot["reference"] for snapshot in item_files} == {
        "UPDATE",
        "PRESERVE",
        "MOVE",
        "CREATE",
    }
    assert all(snapshot["file_id"] is not None for snapshot in item_files)


def test_inbound_accepts_file_move_into_new_box(client, session, make_user) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = Lot(name="Move into new box")
    session.add(lot)
    session.flush()
    source = Box(
        lot_id=lot.id,
        box_number="001",
        current_warehouse_id=2,
        status=BoxStatus.received,
    )
    session.add(source)
    session.flush()
    tracked = BoxFile(
        lot_id=lot.id,
        box_id=source.id,
        reference="MOVE-TO-NEW",
        position=1,
    )
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.awaiting_confirmation,
        requester_user_id=requester.id,
    )
    session.add_all([tracked, request])
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: requester
    inbound_items = [
        {
            "lot": lot.name,
            "box_number": "002",
            "files": [{"reference": "MOVE-TO-NEW"}],
        }
    ]

    preview = client.post(
        f"/api/requests/{request.id}/inbound-completion-preview",
        json={"inbound_items": inbound_items},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["rows"][0]["classification"] == "create"
    assert body["summary"]["files_moved"] == 1
    assert body["rows"][0]["file_impacts"][0]["source_warehouse_id"] == 2

    completed = client.post(
        f"/api/requests/{request.id}/complete",
        json={
            "expected_version": request.version,
            "idempotency_key": "move-file-to-new-box",
            "inbound_items": inbound_items,
            "accept_file_moves": True,
            "inbound_impact_signature": body["impact_signature"],
        },
    )
    assert completed.status_code == 200, completed.text
    session.expire_all()
    target = session.scalar(
        select(Box).where(Box.lot_id == lot.id, Box.box_number == "002")
    )
    assert target is not None
    assert session.get(BoxFile, tracked.id).box_id == target.id


def test_inbound_blocks_file_move_from_reserved_source(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = Lot(name="Reserved file source")
    session.add(lot)
    session.flush()
    source = Box(
        lot_id=lot.id,
        box_number="001",
        current_warehouse_id=2,
        status=BoxStatus.ready_to_return,
    )
    session.add(source)
    session.flush()
    session.add(
        BoxFile(
            lot_id=lot.id,
            box_id=source.id,
            reference="RESERVED-MOVE",
            position=1,
        )
    )
    inbound = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.awaiting_confirmation,
        requester_user_id=requester.id,
    )
    reserved_return = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=2,
        target_warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.approved,
    )
    session.add_all([inbound, reserved_return])
    session.flush()
    session.add(
        BoxRequestItem(
            request_id=reserved_return.id,
            position=1,
            box_id=source.id,
            lot_id=lot.id,
            lot=lot.name,
            box_number=source.box_number,
        )
    )
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: requester

    preview = client.post(
        f"/api/requests/{inbound.id}/inbound-completion-preview",
        json={
            "inbound_items": [
                {
                    "lot": lot.name,
                    "box_number": "002",
                    "files": [{"reference": "RESERVED-MOVE"}],
                }
            ]
        },
    )
    assert preview.status_code == 200, preview.text
    impact = preview.json()["rows"][0]["file_impacts"][0]
    assert impact["action"] == "blocked"
    assert impact["blocked_code"] == "file_source_box_reserved"
    assert f"#{reserved_return.id}" in impact["blocked_message"]
