from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Box,
    BoxEvent,
    BoxEventType,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxStatus,
    Lot,
    LotEvent,
    LotEventType,
    UserRole,
)
from app.models.lots import clean_lot_name, normalize_lot_name
from app.services.boxes import BoxAccessError, BoxConflictError, create_box, reassign_box_lot
from app.services.lots import (
    LotAccessError,
    LotConflictError,
    get_or_create_lot,
    rename_lot,
)
from app.services.requests import create_completed_receipt
from scripts.lot_migration_preflight import analyze_box_rows


def test_lot_name_normalization() -> None:
    assert clean_lot_name("  ACME\t  Lot\n7  ") == "ACME Lot 7"
    assert normalize_lot_name("  ACME\t  Lot\n7  ") == "acme lot 7"
    with pytest.raises(ValueError, match="blank"):
        normalize_lot_name(" \t\n ")


def test_lot_model_and_box_name_compatibility(session: Session) -> None:
    lot = Lot(name="  Alpha\t Lot ", version=1)
    session.add(lot)
    session.flush()
    box = Box(
        box_number="001",
        lot_record=lot,
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    session.add(box)
    session.commit()

    assert lot.name == "Alpha Lot"
    assert lot.normalized_name == "alpha lot"
    assert Lot.__mapper__.version_id_col is Lot.__table__.c.version
    assert box.lot == "Alpha Lot"
    assert box.lot_name == "Alpha Lot"
    assert session.scalar(select(Box).where(Box.lot == "Alpha Lot")) is box


def test_normalized_lot_name_is_globally_unique(session: Session) -> None:
    session.add(Lot(name="Lot A", version=1))
    session.commit()
    session.add(Lot(name="  LOT   A ", version=1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_lot_events_and_request_item_link_keep_snapshot(session: Session) -> None:
    lot = Lot(name="Lot 9", version=1)
    session.add(lot)
    session.flush()
    event = LotEvent(
        lot=lot,
        event_type=LotEventType.created,
        new_name=lot.name,
        reason="test",
    )
    item = BoxRequestItem(
        request_id=1,
        position=1,
        lot_id=lot.id,
        lot="Original Lot 9",
    )

    assert event.event_type is LotEventType.created
    assert item.lot_record is None
    assert item.lot == "Original Lot 9"
    with pytest.raises(ValueError, match="immutable"):
        item.lot = "Changed Lot 9"


def test_preflight_includes_archived_duplicate_identities() -> None:
    report = analyze_box_rows(
        [
            {
                "id": 1,
                "lot": " Lot\tA ",
                "box_number": "001",
                "archived_at": None,
            },
            {
                "id": 2,
                "lot": "LOT A",
                "box_number": "001",
                "archived_at": "2026-01-01",
            },
            {
                "id": 3,
                "lot": "Lot A",
                "box_number": "002",
                "archived_at": None,
            },
        ]
    )

    assert report["safe_to_migrate"] is False
    assert report["normalized_groups"] == [
        {
            "normalized_name": "lot a",
            "display_spellings": ["LOT A", "Lot A"],
            "box_count": 3,
            "archived_box_count": 1,
        }
    ]
    assert report["blocking_duplicates"] == [
        {
            "normalized_name": "lot a",
            "box_number": "001",
            "box_ids": [1, 2],
        }
    ]


def test_preflight_treats_null_lot_as_a_migration_blocker() -> None:
    report = analyze_box_rows(
        [
            {
                "id": 7,
                "lot": None,
                "box_number": "001",
                "archived_at": None,
            }
        ]
    )

    assert report["safe_to_migrate"] is False
    assert report["blank_lot_box_ids"] == [7]
    assert report["normalized_groups"] == []


def test_get_or_create_deduplicates_case_and_whitespace(session: Session, make_user) -> None:
    user = make_user(UserRole.operator)

    first = get_or_create_lot(
        session,
        user=user,
        name="  Canonical\tLot ",
        warehouse_id=1,
    )
    second = get_or_create_lot(
        session,
        user=user,
        name="canonical lot",
        warehouse_id=1,
    )
    session.commit()

    assert second.id == first.id
    assert first.name == "Canonical Lot"
    assert session.scalar(select(LotEvent).where(LotEvent.lot_id == first.id)) is not None


def test_admin_rename_preserves_request_snapshot_and_blocks_collision(
    session: Session, make_user
) -> None:
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    box = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Original Name",
        warehouse_id=1,
    )
    receipt = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[box],
        origin=BoxRequestOrigin.manual_entry,
        note="Snapshot fixture",
    )
    snapshot = receipt.items[0]
    original = box.lot_record

    with pytest.raises(LotAccessError):
        rename_lot(
            session,
            user=operator,
            lot_id=original.id,
            new_name="Denied",
            reason="not an admin",
            expected_version=original.version,
        )
    renamed = rename_lot(
        session,
        user=admin,
        lot_id=original.id,
        new_name="Current Name",
        reason="Correct supplier label",
        expected_version=original.version,
    )
    session.refresh(box)
    session.refresh(snapshot)

    assert renamed.version == 2
    assert box.lot == "Current Name"
    assert snapshot.lot == "Original Name"
    assert session.scalar(
        select(LotEvent).where(
            LotEvent.lot_id == original.id,
            LotEvent.event_type == LotEventType.renamed,
        )
    ) is not None

    collision = get_or_create_lot(
        session,
        user=admin,
        name="Occupied Name",
        warehouse_id=1,
    )
    session.commit()
    with pytest.raises(LotConflictError):
        rename_lot(
            session,
            user=admin,
            lot_id=renamed.id,
            new_name=collision.name,
            reason="Would merge identities",
            expected_version=renamed.version,
        )


def test_admin_box_reassignment_is_audited_and_collision_safe(
    session: Session, make_user
) -> None:
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    source_box = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Source",
        warehouse_id=1,
    )
    target_box = create_box(
        session,
        user=operator,
        box_number="2",
        lot="Target",
        warehouse_id=1,
    )
    source_lot = source_box.lot_record
    target_lot = target_box.lot_record

    with pytest.raises(BoxAccessError):
        reassign_box_lot(
            session,
            user=operator,
            box=source_box,
            lot_id=target_lot.id,
            reason="denied",
            expected_version=source_lot.version,
        )
    reassigned = reassign_box_lot(
        session,
        user=admin,
        box=source_box,
        lot_id=target_lot.id,
        reason="Physical label verified",
        expected_version=source_lot.version,
    )
    assert reassigned.lot_id == target_lot.id
    event = session.scalar(
        select(BoxEvent).where(
            BoxEvent.box_id == source_box.id,
            BoxEvent.event_type == BoxEventType.lot_reassigned,
        )
    )
    assert event is not None
    assert event.event_metadata["from_lot_id"] == source_lot.id
    assert event.event_metadata["to_lot_id"] == target_lot.id

    collision_box = create_box(
        session,
        user=operator,
        box_number="2",
        lot_id=source_lot.id,
        warehouse_id=1,
    )
    session.refresh(source_lot)
    with pytest.raises(BoxConflictError):
        reassign_box_lot(
            session,
            user=admin,
            box=collision_box,
            lot_id=target_lot.id,
            reason="Would collide",
            expected_version=source_lot.version,
        )


def test_box_api_lot_id_compatibility_filter_sort_search_and_export(client) -> None:
    first = client.post(
        "/api/boxes",
        json={"box_number": "41", "lot": "API Lot", "warehouse_id": 1},
    )
    assert first.status_code == 201, first.text
    lot_id = first.json()["lot_id"]
    by_id = client.post(
        "/api/boxes",
        json={"box_number": "42", "lot_id": lot_id, "warehouse_id": 1},
    )
    assert by_id.status_code == 201, by_id.text
    assert by_id.json()["lot"] == "API Lot"

    filtered = client.get(
        "/api/boxes",
        params={"lot_id": lot_id, "sort_by": "lot", "sort_dir": "asc"},
    )
    assert filtered.status_code == 200
    assert {item["box_number"] for item in filtered.json()["items"]} == {"041", "042"}
    searched = client.get("/api/boxes", params={"search": "api lot"})
    assert searched.status_code == 200
    assert searched.json()["total"] == 2
    exported = client.get("/api/exports/boxes.csv", params={"lot_id": lot_id})
    assert exported.status_code == 200
    assert exported.content.count(b"API Lot") == 2
