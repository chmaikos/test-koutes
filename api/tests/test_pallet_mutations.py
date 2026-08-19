from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models import (
    Box,
    BoxEvent,
    BoxEventType,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
    BoxRequestStatus,
    BoxStatus,
    Lot,
    Pallet,
    PalletEvent,
    PalletEventType,
    UserRole,
    Warehouse,
)
from app.services.boxes import restore_archived_box


def _lot(session, name: str) -> Lot:
    lot = Lot(name=name)
    session.add(lot)
    session.flush()
    return lot


def _pallet(session, lot: Lot, number: str, warehouse_id: int = 1) -> Pallet:
    pallet = Pallet(
        lot_id=lot.id,
        pallet_number=number,
    )
    session.add(pallet)
    session.flush()
    return pallet


def _box(
    session,
    lot: Lot,
    number: str,
    *,
    warehouse_id: int = 1,
    pallet: Pallet | None = None,
) -> Box:
    box = Box(
        box_number=number,
        lot_id=lot.id,
        current_warehouse_id=warehouse_id,
        pallet_id=pallet.id if pallet else None,
        status=BoxStatus.received,
    )
    session.add(box)
    session.flush()
    return box


def _reserve(session, box: Box) -> BoxRequest:
    request = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=box.current_warehouse_id,
        target_warehouse_id=2,
        quantity=1,
        status=BoxRequestStatus.submitted,
    )
    session.add(request)
    session.flush()
    session.add(
        BoxRequestItem(
            request_id=request.id,
            position=1,
            box_id=box.id,
            lot_id=box.lot_id,
            lot=box.lot,
            box_number=box.box_number,
            pallet_id=box.pallet_id,
            pallet=box.pallet.pallet_number if box.pallet else None,
        )
    )
    session.flush()
    return request


def test_bulk_assign_reassign_detach_and_audit(client, session) -> None:
    lot = _lot(session, "Assignments")
    other_lot = _lot(session, "Other")
    source = _pallet(session, lot, "Source")
    target = _pallet(session, lot, "Target")
    first = _box(session, lot, "001")
    second = _box(session, lot, "002")
    wrong = _box(session, other_lot, "001")
    session.commit()

    assigned = client.post(
        f"/api/pallets/{source.id}/boxes/assign",
        json={
            "box_ids": [first.id, second.id, wrong.id],
            "reason": "Initial physical sort",
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["updated_box_ids"] == [first.id, second.id]
    assert assigned.json()["skipped"][0]["box_id"] == wrong.id

    reassigned = client.post(
        f"/api/pallets/{target.id}/boxes/assign",
        json={"box_ids": [first.id], "reason": "Correct pallet"},
    )
    assert reassigned.status_code == 200
    detached = client.post(
        f"/api/pallets/{target.id}/boxes/detach",
        json={"box_ids": [first.id], "reason": "Remove loose box"},
    )
    assert detached.status_code == 200
    session.refresh(first)
    assert first.pallet_id is None
    events = session.scalars(
        select(BoxEvent)
        .where(BoxEvent.box_id == first.id)
        .order_by(BoxEvent.id)
    ).all()
    assert [event.event_type for event in events] == [
        BoxEventType.pallet_assigned,
        BoxEventType.pallet_unassigned,
        BoxEventType.pallet_assigned,
        BoxEventType.pallet_unassigned,
    ]
    pallet_types = session.scalars(
        select(PalletEvent.event_type).where(
            PalletEvent.pallet_id.in_([source.id, target.id])
        )
    ).all()
    assert PalletEventType.boxes_assigned in pallet_types
    assert PalletEventType.boxes_unassigned in pallet_types
    session.expire_all()
    assert session.get(Pallet, source.id).is_active is True
    assert session.get(Pallet, target.id).is_active is False


def test_assignment_acl_archived_and_warehouse_guards(
    client, session, make_user
) -> None:
    lot = _lot(session, "ACL")
    target = _pallet(session, lot, "Restricted", warehouse_id=2)
    box = _box(session, lot, "001", warehouse_id=2)
    session.commit()
    operator = make_user(UserRole.operator)
    operator.warehouses = [session.get(Warehouse, 1)]
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: operator
    assert (
        client.post(
            f"/api/pallets/{target.id}/boxes/assign",
            json={"box_ids": [box.id], "reason": "Denied"},
        ).status_code
        == 404
    )

    admin = make_user(UserRole.admin)
    app.dependency_overrides[get_current_user] = lambda: admin
    target.is_active = False
    target.archived_at = datetime.now(UTC)
    session.commit()
    assert (
        client.post(
            f"/api/pallets/{target.id}/boxes/assign",
            json={"box_ids": [box.id], "reason": "Archived"},
        ).status_code
        == 409
    )


def test_pallet_move_is_gone_without_mutation(client, session) -> None:
    lot = _lot(session, "Moves")
    empty = _pallet(session, lot, "Empty")
    loaded = _pallet(session, lot, "Loaded")
    first = _box(session, lot, "001", pallet=loaded)
    second = _box(session, lot, "002", pallet=loaded)
    session.commit()

    empty_move = client.post(
        f"/api/pallets/{empty.id}/move",
        json={"warehouse_id": 2, "expected_version": empty.version},
    )
    assert empty_move.status_code == 410, empty_move.text
    assert "pallets have no location" in empty_move.json()["detail"]

    loaded_move = client.post(
        f"/api/pallets/{loaded.id}/move",
        json={"warehouse_id": 2, "expected_version": loaded.version},
    )
    assert loaded_move.status_code == 410, loaded_move.text
    session.refresh(first)
    session.refresh(second)
    assert first.current_warehouse_id == second.current_warehouse_id == 1
    assert first.pallet_id == second.pallet_id == loaded.id


def test_deprecated_pallet_move_never_cancels_requests(
    client, session
) -> None:
    lot = _lot(session, "Reserved Move")
    pallet = _pallet(session, lot, "Reserved")
    free = _box(session, lot, "001", pallet=pallet)
    reserved = _box(session, lot, "002", pallet=pallet)
    request = _reserve(session, reserved)
    session.commit()
    version = pallet.version

    blocked = client.post(
        f"/api/pallets/{pallet.id}/move",
        json={"warehouse_id": 2, "expected_version": version},
    )
    assert blocked.status_code == 410
    session.refresh(free)
    session.refresh(reserved)
    assert free.current_warehouse_id == reserved.current_warehouse_id == 1

    no_reason = client.post(
        f"/api/pallets/{pallet.id}/move",
        json={"warehouse_id": 2, "expected_version": version, "force": True},
    )
    assert no_reason.status_code == 410
    forced = client.post(
        f"/api/pallets/{pallet.id}/move",
        json={
            "warehouse_id": 2,
            "expected_version": version,
            "force": True,
            "reason": "Emergency relocation",
        },
    )
    assert forced.status_code == 410, forced.text
    session.refresh(request)
    assert request.status == BoxRequestStatus.submitted


def test_box_warehouse_move_preserves_pallet_but_lot_move_detaches(
    client, session
) -> None:
    source_lot = _lot(session, "Source Lot")
    target_lot = _lot(session, "Target Lot")
    pallet = _pallet(session, source_lot, "Attached")
    box = _box(session, source_lot, "001", pallet=pallet)
    session.commit()

    moved = client.patch(
        f"/api/boxes/{box.id}",
        json={"warehouse_id": 2, "note": "Move loose box"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["pallet_id"] == pallet.id
    assert moved.json()["detached_pallet_id"] is None

    box.current_warehouse_id = 1
    box.pallet_id = pallet.id
    session.commit()
    reassigned = client.post(
        f"/api/boxes/{box.id}/reassign-lot",
        json={
            "lot_id": target_lot.id,
            "reason": "Correct lot",
            "expected_lot_version": source_lot.version,
        },
    )
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["pallet_id"] is None


def test_restore_preserves_same_lot_pallet_across_warehouses(session, make_user) -> None:
    admin = make_user(UserRole.admin)
    lot = _lot(session, "Restore")
    pallet = _pallet(session, lot, "Old Warehouse")
    box = _box(session, lot, "001", pallet=pallet)
    box.archived_at = datetime.now(UTC)
    session.commit()

    restored = restore_archived_box(
        session,
        user=admin,
        box_number=box.box_number,
        lot_id=lot.id,
        warehouse_id=2,
        note="Restore elsewhere",
        legacy_allow_unassigned=True,
    )
    assert restored is not None
    assert restored.pallet_id == pallet.id


def test_assigned_box_delete_archives_to_preserve_history(client, session) -> None:
    lot = _lot(session, "Archive Assigned")
    pallet = _pallet(session, lot, "Audit")
    box = _box(session, lot, "001", pallet=pallet)
    session.commit()

    response = client.delete(f"/api/boxes/{box.id}")
    assert response.status_code == 204
    session.refresh(box)
    assert box.archived_at is not None
    assert box.pallet_id == pallet.id


def test_admin_integrity_report_groups_anomalies(client, session) -> None:
    lot = _lot(session, "Integrity")
    other = _lot(session, "Integrity Other")
    pallet = _pallet(session, lot, "P-1")
    inactive = _pallet(session, lot, "Old")
    inactive.is_active = False
    inactive.archived_at = datetime.now(UTC)
    cross_lot = _box(session, other, "001", pallet=pallet)
    valid_multi_warehouse = _box(
        session, lot, "002", warehouse_id=2, pallet=pallet
    )
    inactive_box = _box(session, lot, "003", pallet=inactive)
    unassigned = _box(session, lot, "004")
    session.commit()

    response = client.get("/api/pallets/integrity")
    assert response.status_code == 200, response.text
    report = response.json()
    assert cross_lot.id in report["cross_lot"]["box_ids"]
    assert "cross_warehouse" not in report
    assert valid_multi_warehouse.id not in report["cross_lot"]["box_ids"]
    assert inactive_box.id in report["inactive_pallet_assignments"]["box_ids"]
    assert unassigned.id in report["unassigned_active_boxes"]["box_ids"]
