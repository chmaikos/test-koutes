from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.boxes import Box, BoxStatus
from app.models.lots import Lot
from app.models.requests import (
    BoxRequest,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import UserRole
from app.models.warehouses import ReceiptMode, Warehouse, WarehousePolicyEvent
from app.schemas.requests import InboundBoxItem
from app.services.requests import (
    RequestAccessError,
    RequestConflictError,
    RequestRuleError,
    create_staged_receipt,
    finalize_staged_receipt,
)


def _document(request_id: int, user_id: int) -> BoxRequestDocument:
    return BoxRequestDocument(
        request_id=request_id,
        document_type=BoxRequestDocumentType.delivery_note,
        erp_reference="ERP-1",
        object_key=f"test/{request_id}.pdf",
        original_filename="delivery.pdf",
        content_type="application/pdf",
        size_bytes=8,
        sha256="0" * 64,
        uploaded_by_user_id=user_id,
        is_current=True,
    )


def test_policy_defaults_preserve_legacy_manual_receipt(client, session: Session):
    response = client.post(
        "/api/boxes",
        json={
            "box_number": "1",
            "lot": "LEGACY",
            "pallet_number": "PALLET-LEGACY",
            "warehouse_id": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["box_number"] == "001"
    receipt = session.scalar(
        select(BoxRequest).where(BoxRequest.origin == BoxRequestOrigin.manual_entry)
    )
    assert receipt is not None
    assert receipt.status == BoxRequestStatus.completed


def test_policy_update_is_audited_and_manual_entry_is_staged(
    client, session: Session
):
    updated = client.patch(
        "/api/warehouses/1",
        json={
            "receipt_mode": "admin_review",
            "require_erp_document": True,
            "quarantine_manual_receipts": True,
        },
    )
    assert updated.status_code == 200
    audit = session.scalar(select(WarehousePolicyEvent))
    assert audit is not None
    assert audit.old_policy["receipt_mode"] == "auto_complete"
    assert audit.new_policy["receipt_mode"] == "admin_review"

    staged = client.post(
        "/api/boxes",
        json={
            "box_number": "2",
            "lot": "GOVERNED",
            "pallet_number": "PALLET-GOVERNED",
            "warehouse_id": 1,
            "files": [{"reference": "GOV-FILE", "description": "evidence"}],
        },
    )
    assert staged.status_code == 201
    request_id = staged.json()["staged_receipt_id"]
    request = session.get(BoxRequest, request_id)
    assert request is not None
    assert request.status == BoxRequestStatus.submitted
    assert request.receipt_document_required is True
    assert request.receipt_quarantine is True
    assert request.items[0].box_id is None
    assert request.items[0].lot_id is not None
    assert request.items[0].lot == "GOVERNED"
    assert request.items[0].lot_barcode.startswith("LOT-")
    assert request.items[0].pallet_barcode.startswith("PAL-")
    assert request.items[0].box_barcode is None
    assert request.items[0].file_snapshots[0].barcode is None
    assert session.scalar(select(func.count(Box.id))) == 0

    missing = client.post(
        f"/api/requests/{request_id}/approve",
        json={"expected_version": request.version},
    )
    assert missing.status_code == 400
    session.add(_document(request_id, request.requester_user_id or 0))
    session.commit()
    approved = client.post(
        f"/api/requests/{request_id}/approve",
        json={"expected_version": request.version},
    )
    assert approved.status_code == 200
    assert approved.json()["items"][0]["lot_id"] == request.items[0].lot_id
    completed_item = approved.json()["items"][0]
    assert completed_item["lot_barcode"] == request.items[0].lot_barcode
    assert completed_item["pallet_barcode"] == request.items[0].pallet_barcode
    assert completed_item["box_barcode"].startswith("BOX-")
    assert completed_item["files"][0]["barcode"].startswith("FIL-")
    box = session.scalar(select(Box))
    assert box is not None
    assert box.status == BoxStatus.quarantined
    completion = session.scalar(
        select(BoxRequestEvent)
        .where(
            BoxRequestEvent.request_id == request_id,
            BoxRequestEvent.event_type == BoxRequestEventType.completed,
        )
        .order_by(BoxRequestEvent.id.desc())
    )
    assert completion is not None
    evidence = completion.event_metadata["barcode_snapshots"][0]
    assert evidence["box_barcode"] == completed_item["box_barcode"]
    assert evidence["file_barcodes"] == [completed_item["files"][0]["barcode"]]


def test_two_person_threshold_boundary_and_atomic_capacity(
    session: Session, make_user
):
    importer = make_user(UserRole.admin)
    reviewer = make_user(UserRole.admin)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.two_person_approval_threshold = 2
    warehouse.max_capacity = 1
    session.commit()
    request = create_staged_receipt(
        session,
        user=importer,
        warehouse_id=1,
        origin=BoxRequestOrigin.xlsx_import,
        items=[
            InboundBoxItem(box_number="10", lot="CAP", pallet_number="PALLET-CAP"),
            InboundBoxItem(box_number="11", lot="CAP", pallet_number="PALLET-CAP"),
        ],
    )
    with pytest.raises(RequestAccessError):
        finalize_staged_receipt(
            session,
            request_id=request.id,
            user=importer,
            expected_version=request.version,
        )
    session.rollback()
    with pytest.raises(RequestConflictError):
        finalize_staged_receipt(
            session,
            request_id=request.id,
            user=reviewer,
            expected_version=request.version,
        )
    assert session.scalar(select(func.count(Box.id))) == 0
    session.refresh(request)
    assert request.status == BoxRequestStatus.submitted


def test_archived_restore_is_revalidated_and_quarantine_release_is_audited(
    client, session: Session, make_user
):
    importer = make_user(UserRole.operator)
    reviewer = make_user(UserRole.admin)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.receipt_mode = ReceiptMode.admin_review
    warehouse.quarantine_imports = True
    existing = Box(
        box_number="020",
        lot_record=Lot(name="RESTORE"),
        current_warehouse_id=1,
        status=BoxStatus.returned,
        archived_at=datetime.now(UTC),
    )
    session.add(existing)
    session.commit()
    request = create_staged_receipt(
        session,
        user=importer,
        warehouse_id=1,
        origin=BoxRequestOrigin.xlsx_import,
        restore_archived=True,
        items=[
            InboundBoxItem(
                box_number="20",
                lot="RESTORE",
                pallet_number="PALLET-RESTORE",
            )
        ],
    )
    finalized = finalize_staged_receipt(
        session,
        request_id=request.id,
        user=reviewer,
        expected_version=request.version,
    )
    assert finalized.items[0].box_id == existing.id
    assert finalized.items[0].lot_id == existing.lot_id
    assert finalized.items[0].lot == "RESTORE"
    assert finalized.items[0].pallet_id is not None
    assert finalized.items[0].pallet == "PALLET-RESTORE"
    session.refresh(existing)
    assert existing.status == BoxStatus.quarantined
    assert existing.archived_at is None
    assert existing.pallet_id == finalized.items[0].pallet_id

    released = client.post(
        "/api/boxes/quarantine/release",
        json={"box_ids": [existing.id], "reason": "Inspection passed"},
    )
    assert released.status_code == 200
    session.refresh(existing)
    assert existing.status == BoxStatus.received


def test_document_gate_ignores_non_current_delivery_note(session: Session, make_user):
    importer = make_user(UserRole.operator)
    reviewer = make_user(UserRole.admin)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.require_erp_document = True
    session.commit()
    request = create_staged_receipt(
        session,
        user=importer,
        warehouse_id=1,
        origin=BoxRequestOrigin.manual_entry,
        items=[
            InboundBoxItem(box_number="30", lot="DOC", pallet_number="PALLET-DOC")
        ],
    )
    document = _document(request.id, importer.id)
    document.is_current = False
    session.add(document)
    session.commit()
    with pytest.raises(RequestRuleError):
        finalize_staged_receipt(
            session,
            request_id=request.id,
            user=reviewer,
            expected_version=request.version,
        )
