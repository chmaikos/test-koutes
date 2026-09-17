from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models import (
    Box,
    BoxFile,
    BoxFileEvent,
    BoxFileEventType,
    BoxStatus,
    Lot,
    Pallet,
    UserRole,
)


def _inventory(session):
    source = Lot(name="Integrity source")
    target = Lot(name="Integrity target")
    session.add_all([source, target])
    session.flush()
    pallet = Pallet(
        lot_id=source.id,
        pallet_number="FILES-PALLET",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(pallet)
    session.flush()
    boxes = [
        Box(
            lot_id=source.id,
            pallet_id=pallet.id,
            box_number="001",
            current_warehouse_id=1,
            status=BoxStatus.received,
        ),
        Box(
            lot_id=source.id,
            box_number="002",
            current_warehouse_id=2,
            status=BoxStatus.processing,
        ),
        Box(
            lot_id=target.id,
            box_number="003",
            current_warehouse_id=1,
            status=BoxStatus.received,
        ),
    ]
    session.add_all(boxes)
    session.flush()
    files = [
        BoxFile(
            lot_id=source.id,
            box_id=boxes[0].id,
            reference="FILE-ALPHA",
            description="Needle description",
            barcode="BAR-ALPHA",
            position=1,
        ),
        BoxFile(
            lot_id=source.id,
            box_id=boxes[0].id,
            reference="FILE-ARCHIVED",
            position=2,
            archived_at=datetime.now(UTC),
            archive_reason="Historical folder",
        ),
        BoxFile(
            lot_id=source.id,
            box_id=boxes[1].id,
            reference="FILE-HIDDEN",
            position=1,
        ),
    ]
    session.add_all(files)
    session.commit()
    return source, target, pallet, boxes, files


def test_box_search_file_filters_counts_and_acl_exports(
    client, session, make_user
) -> None:
    source, _target, pallet, boxes, _files = _inventory(session)

    matched = client.get("/api/boxes?search=Needle").json()
    assert matched["total"] == 1
    assert matched["items"][0]["id"] == boxes[0].id
    assert matched["items"][0]["file_count"] == 2
    assert matched["items"][0]["active_file_count"] == 1
    assert matched["items"][0]["archived_file_count"] == 1

    listed = client.get(
        f"/api/files?lot_id={source.id}&pallet_id={pallet.id}"
        "&status=received&sort_by=reference&sort_dir=desc"
    )
    assert listed.status_code == 200
    assert [row["reference"] for row in listed.json()["items"]] == ["FILE-ALPHA"]
    archived = client.get(
        f"/api/files?box_id={boxes[0].id}&activity=archived"
    ).json()
    assert [row["reference"] for row in archived["items"]] == ["FILE-ARCHIVED"]

    lot = client.get(f"/api/lots/{source.id}").json()
    assert lot["active_file_count"] == 2
    assert lot["archived_file_count"] == 1
    pallet_summary = client.get(f"/api/pallets/{pallet.id}").json()
    assert pallet_summary["active_file_count"] == 1
    assert pallet_summary["archived_file_count"] == 1

    box_export = client.get("/api/exports/boxes.csv")
    box_rows = list(
        csv.DictReader(io.StringIO(box_export.content.decode("utf-8-sig")))
    )
    assert len(box_rows) == 3
    exported_box = next(row for row in box_rows if row["Box Number"] == "001")
    assert exported_box["File Count"] == "1"
    assert exported_box["Archived File Count"] == "1"
    assert "FILE-ALPHA" in exported_box["File Summary"]

    viewer = make_user(UserRole.viewer)
    viewer.warehouses = [
        warehouse for warehouse in viewer.warehouses if warehouse.id == 1
    ]
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: viewer
    response = client.get("/api/exports/files.csv")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert {row["Reference"] for row in rows} == {"FILE-ALPHA"}
    assert rows[0]["Warehouse"] == "Building 1"


def test_box_with_file_history_soft_archives(client, session) -> None:
    _source, _target, _pallet, boxes, files = _inventory(session)
    response = client.delete(f"/api/boxes/{boxes[1].id}")
    assert response.status_code == 204
    session.expire_all()
    archived_box = session.get(Box, boxes[1].id)
    assert archived_box.archived_at is not None
    assert archived_box.active_file_count == 0
    assert archived_box.archived_file_count == 1
    assert session.get(BoxFile, files[2].id) is not None


def test_lot_reassignment_updates_files_and_rejects_reference_collision(
    client, session
) -> None:
    source, target, _pallet, boxes, files = _inventory(session)
    moved = client.post(
        f"/api/boxes/{boxes[1].id}/reassign-lot",
        json={
            "lot_id": target.id,
            "reason": "Correct the physical lot placement",
            "expected_lot_version": source.version,
        },
    )
    assert moved.status_code == 200, moved.text
    session.expire_all()
    assert session.get(BoxFile, files[2].id).lot_id == target.id
    event = session.scalar(
        select(BoxFileEvent)
        .where(
            BoxFileEvent.file_id == files[2].id,
            BoxFileEvent.event_type == BoxFileEventType.lot_reassigned,
        )
    )
    assert event is not None

    conflicting = BoxFile(
        lot_id=target.id,
        box_id=boxes[2].id,
        reference="FILE-ALPHA",
        position=1,
    )
    session.add(conflicting)
    session.commit()
    source = session.get(Lot, source.id)
    blocked = client.post(
        f"/api/boxes/{boxes[0].id}/reassign-lot",
        json={
            "lot_id": target.id,
            "reason": "This reassignment must remain atomic",
            "expected_lot_version": source.version,
            "detach_pallet": True,
        },
    )
    assert blocked.status_code == 409
    session.expire_all()
    assert session.get(Box, boxes[0].id).lot_id == source.id
    assert session.get(BoxFile, files[0].id).lot_id == source.id


def test_lot_merge_file_collisions_require_archived_overwrite(
    client, session
) -> None:
    source, target, _pallet, boxes, files = _inventory(session)
    target_file = BoxFile(
        lot_id=target.id,
        box_id=boxes[2].id,
        reference="FILE-HIDDEN",
        position=1,
    )
    session.add(target_file)
    session.commit()

    payload = {
        "target_lot_id": target.id,
        "reason": "Consolidate duplicate lot identities",
        "expected_source_version": source.version,
        "expected_target_version": target.version,
    }
    blocked = client.post(f"/api/lots/{source.id}/merge", json=payload)
    assert blocked.status_code == 409
    candidate = blocked.json()["detail"]["merge_candidate"]
    assert candidate["active_file_reference_collision_count"] == 1
    assert candidate["file_reference_collisions"][0]["active_file_ids"]

    session.expire_all()
    files[2].archived_at = datetime.now(UTC)
    files[2].archive_reason = "Old source copy"
    target_file.archived_at = datetime.now(UTC)
    target_file.archive_reason = "Target history survives"
    session.commit()
    source = session.get(Lot, source.id)
    target = session.get(Lot, target.id)
    preview = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            **payload,
            "expected_source_version": source.version,
            "expected_target_version": target.version,
        },
    )
    assert preview.status_code == 409
    candidate = preview.json()["detail"]["merge_candidate"]
    assert candidate["archived_file_reference_collision_count"] == 1
    merged = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            **payload,
            "expected_source_version": source.version,
            "expected_target_version": target.version,
            "overwrite_archived_collisions": True,
            "expected_collision_signature": candidate["collision_signature"],
        },
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["moved_file_count"] == 2
    assert merged.json()["overwritten_archived_file_count"] == 1
    session.expire_all()
    assert session.get(BoxFile, files[2].id) is None
    assert session.get(BoxFile, target_file.id) is not None
    assert session.get(BoxFile, files[0].id).lot_id == target.id


def test_admin_file_integrity_endpoint(client, session) -> None:
    _inventory(session)
    response = client.get("/api/files/integrity")
    assert response.status_code == 200
    assert response.json()["safe"] is True
