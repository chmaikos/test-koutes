from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models import (
    Box,
    BoxFile,
    BoxFileEvent,
    BoxFileEventType,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestEvent,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
    BoxStatus,
    Lot,
)
from app.models.users import UserRole
from app.services.boxes import bulk_update_boxes


def _inventory(session, *, warehouses: tuple[int, ...] = (1, 1)):
    lot = Lot(name="Tracked records")
    session.add(lot)
    session.flush()
    boxes = [
        Box(
            lot_id=lot.id,
            box_number=f"{index:03d}",
            current_warehouse_id=warehouse_id,
            status=BoxStatus.received,
        )
        for index, warehouse_id in enumerate(warehouses, start=1)
    ]
    session.add_all(boxes)
    session.commit()
    return lot, boxes


def test_file_api_crud_normalization_hierarchy_and_events(client, session) -> None:
    lot, boxes = _inventory(session)

    created = client.post(
        "/api/files",
        json={
            "box_id": boxes[0].id,
            "reference": "  Client   Record  ",
            "description": "  Original  ",
            "barcode": " BC-1 ",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["reference"] == "Client Record"
    assert body["description"] == "Original"
    assert body["barcode"] == "BC-1"
    assert body["lot_id"] == lot.id
    assert body["lot"] == lot.name
    assert body["box"] == "001"
    assert body["warehouse"] == "Building 1"
    assert body["status"] == "received"
    assert body["is_active"] is True
    assert body["created_by_user_id"] is not None

    duplicate = client.post(
        "/api/files",
        json={
            "box_id": boxes[1].id,
            "reference": "client record",
            "barcode": "BC-1",
        },
    )
    assert duplicate.status_code == 409
    same_barcode = client.post(
        "/api/files",
        json={
            "box_id": boxes[1].id,
            "reference": "Different record",
            "barcode": "BC-1",
        },
    )
    assert same_barcode.status_code == 201

    file_id = body["id"]
    updated = client.patch(
        f"/api/files/{file_id}",
        json={
            "expected_version": body["version"],
            "description": "Revised",
            "barcode": None,
        },
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["description"] == "Revised"
    assert updated_body["barcode"] is None

    stale = client.patch(
        f"/api/files/{file_id}",
        json={"expected_version": body["version"], "description": "stale"},
    )
    assert stale.status_code == 409
    assert "version conflict" in stale.json()["detail"]

    events = client.get(f"/api/files/{file_id}/events")
    assert events.status_code == 200
    event_types = [event["event_type"] for event in events.json()]
    assert event_types == ["updated", "created"]
    assert events.json()[0]["before_snapshot"]["description"] == "Original"
    assert events.json()[0]["after_snapshot"]["description"] == "Revised"


def test_move_archive_restore_compacts_positions(client, session) -> None:
    _, boxes = _inventory(session)
    first = client.post(
        "/api/files",
        json={"box_id": boxes[0].id, "reference": "A"},
    ).json()
    second = client.post(
        "/api/files",
        json={"box_id": boxes[0].id, "reference": "B"},
    ).json()
    third = client.post(
        "/api/files",
        json={"box_id": boxes[1].id, "reference": "C"},
    ).json()

    moved = client.post(
        f"/api/files/{second['id']}/move",
        json={
            "box_id": boxes[1].id,
            "position": 1,
            "expected_version": second["version"],
        },
    )
    assert moved.status_code == 200
    assert moved.json()["box_id"] == boxes[1].id
    target = client.get(f"/api/files?box_id={boxes[1].id}").json()["items"]
    assert [(item["reference"], item["position"]) for item in target] == [
        ("B", 1),
        ("C", 2),
    ]
    source = client.get(f"/api/files?box_id={boxes[0].id}").json()["items"]
    assert [(item["reference"], item["position"]) for item in source] == [("A", 1)]

    archived = client.post(
        f"/api/files/{second['id']}/archive",
        json={
            "expected_version": moved.json()["version"],
            "reason": "Duplicate physical folder",
        },
    )
    assert archived.status_code == 200
    assert archived.json()["is_active"] is False
    active_target = client.get(f"/api/files?box_id={boxes[1].id}").json()["items"]
    assert [(item["reference"], item["position"]) for item in active_target] == [("C", 1)]
    including_archived = client.get(
        f"/api/files?box_id={boxes[1].id}&include_inactive=true&activity=all"
    ).json()["items"]
    assert [(item["reference"], item["is_active"]) for item in including_archived] == [
        ("C", True),
        ("B", False),
    ]
    archived_only = client.get(
        f"/api/files?box_id={boxes[1].id}&include_inactive=true&activity=archived"
    ).json()["items"]
    assert [item["reference"] for item in archived_only] == ["B"]

    restored = client.post(
        f"/api/files/{second['id']}/restore",
        json={
            "expected_version": archived.json()["version"],
            "position": 1,
            "reason": "Folder found",
        },
    )
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    all_target = client.get(f"/api/files?box_id={boxes[1].id}").json()["items"]
    assert [(item["reference"], item["position"]) for item in all_target] == [
        ("B", 1),
        ("C", 2),
    ]
    assert first["position"] == 1
    assert third["position"] == 1


def test_cross_lot_move_and_destination_acl_are_rejected(
    client, session, make_user
) -> None:
    _, boxes = _inventory(session, warehouses=(1, 2))
    other_lot = Lot(name="Other lot")
    session.add(other_lot)
    session.flush()
    other_box = Box(
        lot_id=other_lot.id,
        box_number="001",
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    session.add(other_box)
    session.commit()
    created = client.post(
        "/api/files",
        json={"box_id": boxes[0].id, "reference": "ACL-1"},
    ).json()

    cross_lot = client.post(
        f"/api/files/{created['id']}/move",
        json={
            "box_id": other_box.id,
            "expected_version": created["version"],
        },
    )
    assert cross_lot.status_code == 409

    operator = make_user(UserRole.operator)
    operator.warehouses = [
        warehouse for warehouse in operator.warehouses if warehouse.id == 1
    ]
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: operator
    denied = client.post(
        f"/api/files/{created['id']}/move",
        json={"box_id": boxes[1].id, "expected_version": created["version"]},
    )
    assert denied.status_code == 403


def test_read_acl_and_role_gate(client, session, make_user) -> None:
    _, boxes = _inventory(session, warehouses=(1, 2))
    first = BoxFile(lot_id=boxes[0].lot_id, box_id=boxes[0].id, reference="VISIBLE", position=1)
    second = BoxFile(lot_id=boxes[1].lot_id, box_id=boxes[1].id, reference="HIDDEN", position=1)
    session.add_all([first, second])
    session.commit()

    viewer = make_user(UserRole.viewer)
    viewer.warehouses = [
        warehouse for warehouse in viewer.warehouses if warehouse.id == 1
    ]
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: viewer
    listed = client.get("/api/files")
    assert listed.status_code == 200
    assert [item["reference"] for item in listed.json()["items"]] == ["VISIBLE"]
    assert client.get(f"/api/files/{second.id}").status_code == 404
    assert (
        client.post(
            "/api/files",
            json={"box_id": boxes[0].id, "reference": "NO-WRITE"},
        ).status_code
        == 403
    )
    assert client.get("/api/files?include_inactive=true").status_code == 403
    first.archived_at = datetime.now(UTC)
    first.archive_reason = "Archived but still ACL-visible"
    session.commit()
    archived = client.get("/api/files?activity=archived")
    assert archived.status_code == 200
    assert [item["reference"] for item in archived.json()["items"]] == ["VISIBLE"]
    assert client.get(f"/api/files/{first.id}").status_code == 404
    assert (
        client.get(f"/api/files/{first.id}?include_archived=true").status_code
        == 200
    )


def test_event_snapshots_redact_inaccessible_historical_hierarchy(
    client, session, make_user
) -> None:
    _, boxes = _inventory(session, warehouses=(2, 1))
    created = client.post(
        "/api/files",
        json={"box_id": boxes[0].id, "reference": "HISTORICAL-ACL"},
    ).json()
    moved = client.post(
        f"/api/files/{created['id']}/move",
        json={
            "box_id": boxes[1].id,
            "expected_version": created["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    move_event_row = session.scalar(
        select(BoxFileEvent)
        .where(
            BoxFileEvent.file_id == created["id"],
            BoxFileEvent.event_type == BoxFileEventType.moved,
        )
        .order_by(BoxFileEvent.id.desc())
    )
    assert move_event_row is not None
    move_event_row.event_metadata = {
        **move_event_row.event_metadata,
        "source_warehouse_id": 2,
        "source_pallet_id": 42,
    }
    session.commit()

    viewer = make_user(UserRole.viewer)
    viewer.warehouses = [
        warehouse for warehouse in viewer.warehouses if warehouse.id == 1
    ]
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: viewer
    events = client.get(f"/api/files/{created['id']}/events")
    assert events.status_code == 200, events.text
    move_event = next(
        event for event in events.json() if event["event_type"] == "moved"
    )
    assert move_event["before_snapshot"]["warehouse_id"] is None
    assert move_event["before_snapshot"]["box_id"] is None
    assert move_event["after_snapshot"]["warehouse_id"] == 1
    assert move_event["metadata"]["source_warehouse_id"] is None
    assert move_event["metadata"]["source_pallet_id"] is None


def test_create_rejects_archived_box_and_merged_lot(client, session) -> None:
    lot, boxes = _inventory(session)
    boxes[0].archived_at = datetime.now(UTC)
    boxes[0].archive_reason = "Retired"
    session.commit()
    archived_box = client.post(
        "/api/files",
        json={"box_id": boxes[0].id, "reference": "NO-ARCHIVED-BOX"},
    )
    assert archived_box.status_code == 409

    target = Lot(name="Surviving lot")
    session.add(target)
    session.flush()
    lot.normalized_name = None
    lot.merged_into_lot_id = target.id
    lot.merged_at = datetime.now(UTC)
    session.commit()
    boxes[1].archived_at = None
    merged_lot = client.post(
        "/api/files",
        json={"box_id": boxes[1].id, "reference": "NO-MERGED-LOT"},
    )
    assert merged_lot.status_code == 409


def test_reservation_guard_metadata_exception_and_force_audit(
    client, session
) -> None:
    _, boxes = _inventory(session)
    created = client.post(
        "/api/files",
        json={"box_id": boxes[0].id, "reference": "RESERVED"},
    ).json()
    request = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        target_warehouse_id=2,
        quantity=1,
        status=BoxRequestStatus.approved,
        origin=BoxRequestOrigin.workflow,
    )
    session.add(request)
    session.flush()
    session.add(
        BoxRequestItem(
            request_id=request.id,
            position=1,
            box_id=boxes[0].id,
            lot_id=boxes[0].lot_id,
            lot=boxes[0].lot,
            box_number=boxes[0].box_number,
        )
    )
    session.commit()

    metadata_only = client.patch(
        f"/api/files/{created['id']}",
        json={
            "expected_version": created["version"],
            "description": "Snapshot-safe metadata",
        },
    )
    assert metadata_only.status_code == 200

    archive_blocked = client.post(
        f"/api/files/{created['id']}/archive",
        json={
            "expected_version": metadata_only.json()["version"],
            "reason": "Cannot archive while reserved",
        },
    )
    assert archive_blocked.status_code == 409
    blocked = client.patch(
        f"/api/files/{created['id']}",
        json={
            "expected_version": metadata_only.json()["version"],
            "reference": "NEW-IDENTITY",
        },
    )
    assert blocked.status_code == 409
    forced = client.patch(
        f"/api/files/{created['id']}",
        json={
            "expected_version": metadata_only.json()["version"],
            "reference": "NEW-IDENTITY",
            "force": True,
            "reason": "Correcting a mislabeled reserved folder",
        },
    )
    assert forced.status_code == 200
    session.expire_all()
    assert session.get(BoxRequest, request.id).status == BoxRequestStatus.cancelled
    request_event = session.scalar(
        select(BoxRequestEvent)
        .where(BoxRequestEvent.request_id == request.id)
        .order_by(BoxRequestEvent.id.desc())
    )
    assert request_event is not None
    assert request_event.event_metadata["admin_override"] is True
    file_event = session.scalar(
        select(BoxFileEvent)
        .where(BoxFileEvent.file_id == created["id"])
        .order_by(BoxFileEvent.id.desc())
    )
    assert file_event is not None
    assert file_event.event_metadata["cancelled_request_ids"] == [request.id]


def test_batch_box_move_and_status_emit_inherited_file_events(
    session, make_user
) -> None:
    _, boxes = _inventory(session)
    session.add_all(
        [
            BoxFile(
                lot_id=boxes[0].lot_id,
                box_id=boxes[0].id,
                reference="CHILD-1",
                position=1,
            ),
            BoxFile(
                lot_id=boxes[1].lot_id,
                box_id=boxes[1].id,
                reference="CHILD-2",
                position=1,
            ),
        ]
    )
    session.commit()
    operator = make_user(UserRole.operator)

    outcome = bulk_update_boxes(
        session,
        user=operator,
        box_ids=[box.id for box in boxes],
        new_warehouse_id=2,
        new_status=BoxStatus.processing,
        note="Batch transfer",
    )
    assert len(outcome.updated) == 2
    events = session.scalars(
        select(BoxFileEvent).order_by(BoxFileEvent.file_id, BoxFileEvent.id)
    ).all()
    assert [event.event_type for event in events] == [
        BoxFileEventType.box_moved,
        BoxFileEventType.box_status_changed,
        BoxFileEventType.box_moved,
        BoxFileEventType.box_status_changed,
    ]
    status_event = events[1]
    assert status_event.before_snapshot["status"] == "received"
    assert status_event.after_snapshot["status"] == "processing"
    assert status_event.after_snapshot["warehouse_id"] == 2
