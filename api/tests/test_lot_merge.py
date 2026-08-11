from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite

from app.deps import get_current_user
from app.events import bus
from app.main import app
from app.models import (
    Box,
    BoxEvent,
    BoxEventType,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestDiscrepancy,
    BoxRequestDiscrepancyPhoto,
    BoxRequestDiscrepancyType,
    BoxRequestDocument,
    BoxRequestDocumentType,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
    BoxStatus,
    InAppNotification,
    Lot,
    LotEvent,
    LotEventType,
    LotPurgeCleanupStatus,
    LotPurgeEvent,
    UserRole,
)
from app.schemas.lots import LotMerge
from app.services.boxes import BoxRuleError, create_box
from app.services.lots import (
    _active_lot_use_statement,
    _exclusive_lot_statement,
    _merge_box_event_lock_statement,
    _merge_box_lock_statement,
    _merge_candidate,
    _merge_discrepancy_lock_statement,
    _merge_request_item_lock_statement,
    _merge_request_lock_statement,
    merge_lots,
)
from app.services.requests import create_completed_receipt


def _box(client, number: str, lot: str) -> dict:
    response = client.post(
        "/api/boxes",
        json={"box_number": number, "lot": lot, "warehouse_id": 1},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_lot_identity_lock_sql_is_postgresql_specific_and_ordered():
    shared = str(
        _active_lot_use_statement([9, 3]).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    exclusive = str(
        _exclusive_lot_statement([9, 3]).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    sqlite_shared = str(
        _active_lot_use_statement([9, 3]).compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "lots.id IN (3, 9)" in shared
    assert "ORDER BY lots.id" in shared
    assert shared.endswith("FOR SHARE")
    assert exclusive.endswith("FOR UPDATE")
    assert "FOR SHARE" not in sqlite_shared
    assert "FOR UPDATE" not in sqlite_shared


def test_box_lock_targets_boxes_when_eager_lot_join_is_present():
    statement = _merge_box_lock_statement([9, 3])
    postgres_sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "LEFT OUTER JOIN lots" in postgres_sql
    assert postgres_sql.endswith("FOR UPDATE OF boxes")
    for statement, table_name in (
        (_merge_request_item_lock_statement(3, [8, 4]), "box_request_items"),
        (_merge_discrepancy_lock_statement([8, 4]), "box_request_discrepancies"),
        (_merge_request_lock_statement([8, 4]), "box_requests"),
        (_merge_box_event_lock_statement([8, 4]), "box_events"),
    ):
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert f"FOR UPDATE OF {table_name}" in sql


def test_rename_collision_candidate_normalizes_and_counts_archived_overlap(
    client, session
):
    source = _box(client, "1", " Source   Lot ")
    target = _box(client, "1", "Target Lot")
    archived = session.get(Box, target["id"])
    archived.archived_at = datetime.now(UTC)
    session.commit()
    source_lot = session.get(Lot, source["lot_id"])

    response = client.patch(
        f"/api/lots/{source_lot.id}/rename",
        json={
            "new_name": "  TARGET\tLOT ",
            "reason": "Duplicate supplier identity",
            "expected_version": source_lot.version,
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "name_collision"
    candidate = detail["merge_candidate"]
    assert candidate["source"]["id"] == source_lot.id
    assert candidate["target"]["id"] == target["lot_id"]
    assert candidate["target"]["name"] == "Target Lot"
    assert candidate["merge_allowed"] is False
    assert candidate["overlapping_box_numbers"] == ["001"]
    assert candidate["overlapping_box_count"] == 1
    assert candidate["hard_overlap_count"] == 0
    assert candidate["resolvable_archived_collision_count"] == 1
    assert candidate["merge_allowed_with_archived_overwrite"] is True
    assert candidate["requires_explicit_overwrite"] is True
    collision = candidate["resolvable_archived_collisions"][0]
    assert collision["box_number"] == "001"
    assert collision["survivor_box_id"] == source["id"]
    assert collision["survivor_lot_side"] == "source"
    assert collision["removed_box_id"] == target["id"]
    assert collision["removed_lot_side"] == "target"
    assert collision["request_item_relink_count"] == 1
    assert collision["discrepancy_relink_count"] == 0
    assert collision["box_event_delete_count"] >= 1
    assert len(candidate["collision_signature"]) == 64


def test_collision_plan_classifies_all_survivors_and_history_counts(
    session, make_user
):
    operator = make_user(UserRole.operator)
    source_boxes = [
        create_box(
            session,
            user=operator,
            box_number=str(number),
            lot="Plan Source",
            warehouse_id=1,
        )
        for number in range(1, 5)
    ]
    target_boxes = [
        create_box(
            session,
            user=operator,
            box_number=str(number),
            lot="Plan Target",
            warehouse_id=1,
        )
        for number in range(1, 5)
    ]
    receipt = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[*source_boxes, *target_boxes],
        origin=BoxRequestOrigin.manual_entry,
        note="collision history",
    )
    archived_at = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
    target_boxes[1].archived_at = archived_at
    source_boxes[2].archived_at = archived_at
    source_boxes[3].archived_at = archived_at
    target_boxes[3].archived_at = archived_at
    items_by_box_id = {item.box_id: item for item in receipt.items}
    removed_boxes = (target_boxes[1], source_boxes[2], source_boxes[3])
    session.add_all(
        [
            BoxRequestDiscrepancy(
                request_id=receipt.id,
                request_item_id=items_by_box_id[box.id].id,
                box_id=box.id,
                discrepancy_type=BoxRequestDiscrepancyType.damaged,
                created_by_user_id=operator.id,
            )
            for box in removed_boxes
        ]
    )
    session.commit()

    source = source_boxes[0].lot_record
    target = target_boxes[0].lot_record
    plan = _merge_candidate(session, source, target)
    repeated = _merge_candidate(session, source, target)

    assert plan.collision_signature == repeated.collision_signature
    assert plan.merge_allowed is False
    assert plan.merge_allowed_with_archived_overwrite is False
    assert plan.requires_explicit_overwrite is True
    assert plan.overlapping_box_count == 4
    assert plan.hard_overlap_count == 1
    assert plan.hard_overlaps[0].box_number == "001"
    assert plan.hard_overlaps[0].source_active_box_ids == [source_boxes[0].id]
    assert plan.hard_overlaps[0].target_active_box_ids == [target_boxes[0].id]

    collisions = {
        collision.box_number: collision
        for collision in plan.resolvable_archived_collisions
    }
    assert set(collisions) == {"002", "003", "004"}
    assert (
        collisions["002"].survivor_box_id,
        collisions["002"].survivor_lot_side,
        collisions["002"].removed_box_id,
        collisions["002"].removed_lot_side,
    ) == (source_boxes[1].id, "source", target_boxes[1].id, "target")
    assert (
        collisions["003"].survivor_box_id,
        collisions["003"].survivor_lot_side,
        collisions["003"].removed_box_id,
        collisions["003"].removed_lot_side,
    ) == (target_boxes[2].id, "target", source_boxes[2].id, "source")
    assert (
        collisions["004"].survivor_box_id,
        collisions["004"].survivor_lot_side,
        collisions["004"].removed_box_id,
        collisions["004"].removed_lot_side,
    ) == (target_boxes[3].id, "target", source_boxes[3].id, "source")
    assert all(
        collision.request_item_relink_count == 1
        and collision.discrepancy_relink_count == 1
        and collision.box_event_delete_count >= 1
        for collision in collisions.values()
    )

    prior_signature = plan.collision_signature
    removed_for_event = target_boxes[1]
    session.add(
        BoxEvent(
            box_id=removed_for_event.id,
            warehouse_id=removed_for_event.current_warehouse_id,
            event_type=BoxEventType.archived,
            from_status=removed_for_event.status,
            to_status=removed_for_event.status,
            from_warehouse_id=removed_for_event.current_warehouse_id,
            to_warehouse_id=removed_for_event.current_warehouse_id,
            user_id=operator.id,
            note="signature event note must not enter the merge signature",
        )
    )
    session.flush()
    event_signature = _merge_candidate(session, source, target).collision_signature
    assert event_signature != prior_signature

    receipt.version += 1
    session.flush()
    request_signature = _merge_candidate(session, source, target).collision_signature
    assert request_signature != event_signature

    source.version += 1
    assert _merge_candidate(session, source, target).collision_signature != request_signature


def test_collision_plan_bounds_preview_lists(session, make_user):
    operator = make_user(UserRole.operator)
    source = Lot(
        name="Bounded Source",
        normalized_name="bounded source",
        created_by_user_id=operator.id,
        updated_by_user_id=operator.id,
    )
    target = Lot(
        name="Bounded Target",
        normalized_name="bounded target",
        created_by_user_id=operator.id,
        updated_by_user_id=operator.id,
    )
    session.add_all([source, target])
    session.flush()
    archived_at = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)
    for number in range(101):
        canonical = f"{number:03d}"
        session.add_all(
            [
                Box(
                    box_number=canonical,
                    lot_id=source.id,
                    current_warehouse_id=1,
                    status=BoxStatus.received,
                    updated_by_user_id=operator.id,
                ),
                Box(
                    box_number=canonical,
                    lot_id=target.id,
                    current_warehouse_id=1,
                    status=BoxStatus.returned,
                    archived_at=archived_at,
                    updated_by_user_id=operator.id,
                ),
            ]
        )
    session.commit()

    plan = _merge_candidate(session, source, target)

    assert plan.overlapping_box_count == 101
    assert len(plan.overlapping_box_numbers) == 100
    assert plan.overlap_list_truncated is True
    assert plan.resolvable_archived_collision_count == 101
    assert len(plan.resolvable_archived_collisions) == 100
    assert plan.resolvable_archived_collisions_truncated is True
    assert plan.hard_overlap_count == 0
    assert plan.hard_overlaps_truncated is False
    assert plan.merge_allowed_with_archived_overwrite is True


def test_merge_payload_requires_consistent_overwrite_signature():
    base = {
        "target_lot_id": 2,
        "reason": "confirmed duplicate",
        "expected_source_version": 1,
        "expected_target_version": 1,
    }
    normal = LotMerge.model_validate(base)
    assert normal.overwrite_archived_collisions is False
    assert normal.expected_collision_signature is None

    missing_signature = LotMerge.model_validate(
        {**base, "overwrite_archived_collisions": True}
    )
    assert missing_signature.expected_collision_signature is None
    with pytest.raises(ValidationError, match="only allowed when"):
        LotMerge.model_validate(
            {**base, "expected_collision_signature": "a" * 64}
        )
    with pytest.raises(ValidationError):
        LotMerge.model_validate(
            {
                **base,
                "overwrite_archived_collisions": True,
                "expected_collision_signature": "not-a-sha256",
            }
        )


def test_merge_moves_boxes_and_links_but_preserves_snapshots_and_audits(
    client, session, make_user, monkeypatch
):
    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr(bus, "publish", capture)
    operator = make_user(UserRole.operator)
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
    receipt = create_completed_receipt(
        session,
        user=operator,
        warehouse_id=1,
        boxes=[source_box],
        origin=BoxRequestOrigin.manual_entry,
        note="merge snapshot",
    )
    source = source_box.lot_record
    target = target_box.lot_record
    source_version = source.version
    target_version = target.version

    response = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            "target_lot_id": target.id,
            "reason": "Same supplier lot confirmed",
            "expected_source_version": source_version,
            "expected_target_version": target_version,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["moved_box_count"] == 1
    assert result["moved_request_item_count"] == 1
    assert result["overwritten_archived_box_count"] == 0
    assert result["relinked_request_item_count"] == 0
    assert result["relinked_discrepancy_count"] == 0
    session.expire_all()
    merged_source = session.get(Lot, source.id)
    current_target = session.get(Lot, target.id)
    moved_box = session.get(Box, source_box.id)
    item = session.get(BoxRequestItem, receipt.items[0].id)
    assert merged_source.name == "Source"
    assert merged_source.normalized_name is None
    assert merged_source.merged_into_lot_id == target.id
    assert merged_source.merged_at is not None
    assert merged_source.version == source_version + 1
    assert moved_box.lot_id == target.id
    assert item.lot_id == target.id
    assert item.lot == "Source"
    assert current_target.version == target_version + 1
    events = session.scalars(
        select(LotEvent)
        .where(LotEvent.event_type == LotEventType.merged)
        .order_by(LotEvent.lot_id)
    ).all()
    assert {event.lot_id for event in events} == {source.id, target.id}
    assert all(event.reason == "Same supplier lot confirmed" for event in events)
    assert events[0].event_metadata["moved_box_count"] == 1
    box_event = session.scalar(
        select(BoxEvent).where(
            BoxEvent.box_id == source_box.id,
            BoxEvent.event_type == BoxEventType.lot_reassigned,
        )
    )
    assert box_event is not None
    assert box_event.event_metadata["operation"] == "lot_merge"
    assert any(kind == "lot.merged" for kind, _data in published)

    listing = client.get("/api/lots")
    assert source.id not in {lot["id"] for lot in listing.json()["items"]}
    options = client.get("/api/lots/options", params={"search": "Source"})
    assert options.json()["total"] == 0
    detail = client.get(f"/api/lots/{source.id}")
    assert detail.status_code == 200
    assert detail.json()["state"] == "merged"
    assert detail.json()["merged_into"]["id"] == target.id

    with pytest.raises(BoxRuleError, match="active lot.*not found"):
        create_box(
            session,
            user=operator,
            box_number="3",
            lot_id=source.id,
            warehouse_id=1,
        )
    session.rollback()

    recreated = client.post(
        "/api/lots",
        json={"name": " Source ", "warehouse_id": 1},
    )
    assert recreated.status_code == 201
    assert recreated.json()["id"] not in {source.id, target.id}


def test_merge_conflicts_are_atomic_for_overlap_and_stale_versions(client, session):
    source = _box(client, "7", "Merge Source")
    target = _box(client, "7", "Merge Target")
    source_lot = session.get(Lot, source["lot_id"])
    target_lot = session.get(Lot, target["lot_id"])

    source_lot.version += 1
    session.commit()
    stale_source = client.post(
        f"/api/lots/{source_lot.id}/merge",
        json={
            "target_lot_id": target_lot.id,
            "reason": "Stale source confirmation",
            "expected_source_version": source_lot.version - 1,
            "expected_target_version": target_lot.version,
        },
    )
    assert stale_source.status_code == 409
    assert stale_source.json()["detail"]["code"] == "source_version_conflict"
    assert (
        stale_source.json()["detail"]["merge_candidate"]["source"]["version"]
        == source_lot.version
    )

    overlap = client.post(
        f"/api/lots/{source_lot.id}/merge",
        json={
            "target_lot_id": target_lot.id,
            "reason": "Should roll back",
            "expected_source_version": source_lot.version,
            "expected_target_version": target_lot.version,
        },
    )
    assert overlap.status_code == 409
    assert overlap.json()["detail"]["code"] == "box_number_overlap"
    overlap_candidate = overlap.json()["detail"]["merge_candidate"]
    assert overlap_candidate["hard_overlap_count"] == 1
    assert overlap_candidate["resolvable_archived_collision_count"] == 0
    assert overlap_candidate["merge_allowed_with_archived_overwrite"] is False
    assert overlap_candidate["requires_explicit_overwrite"] is False
    session.expire_all()
    assert session.get(Box, source["id"]).lot_id == source_lot.id
    assert session.get(Lot, source_lot.id).merged_into_lot_id is None

    target_lot = session.get(Lot, target_lot.id)
    target_lot.version += 1
    session.commit()
    stale = client.post(
        f"/api/lots/{source_lot.id}/merge",
        json={
            "target_lot_id": target_lot.id,
            "reason": "Stale confirmation",
            "expected_source_version": source_lot.version,
            "expected_target_version": target_lot.version - 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "target_version_conflict"
    assert stale.json()["detail"]["merge_candidate"]["target"]["version"] == target_lot.version


def test_archived_overwrite_executes_with_active_source_survivor(
    client, session, monkeypatch
):
    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr(bus, "publish", capture)
    source = _box(client, "9", "Overwrite Source")
    target = _box(client, "9", "Overwrite Target")
    target_box = session.get(Box, target["id"])
    target_box.archived_at = datetime.now(UTC)
    session.commit()
    source_lot = session.get(Lot, source["lot_id"])
    target_lot = session.get(Lot, target["lot_id"])
    rename_preview = client.patch(
        f"/api/lots/{source_lot.id}/rename",
        json={
            "new_name": target_lot.name,
            "reason": "Preview collision plan",
            "expected_version": source_lot.version,
        },
    )
    signature = rename_preview.json()["detail"]["merge_candidate"][
        "collision_signature"
    ]
    payload = {
        "target_lot_id": target_lot.id,
        "reason": "Explicit archived collision overwrite",
        "expected_source_version": source_lot.version,
        "expected_target_version": target_lot.version,
        "overwrite_archived_collisions": True,
        "expected_collision_signature": "0" * 64,
    }

    stale = client.post(f"/api/lots/{source_lot.id}/merge", json=payload)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "overlap_signature_mismatch"
    payload["expected_collision_signature"] = signature
    merged = client.post(f"/api/lots/{source_lot.id}/merge", json=payload)

    assert merged.status_code == 200, merged.text
    result = merged.json()
    assert result["overwritten_archived_box_count"] == 1
    assert result["relinked_request_item_count"] == 1
    assert result["deleted_box_event_count"] >= 1
    session.expire_all()
    assert session.get(Box, source["id"]).lot_id == target_lot.id
    assert session.get(Box, target["id"]) is None
    assert session.get(Lot, source_lot.id).merged_into_lot_id == target_lot.id
    target_items = session.scalars(
        select(BoxRequestItem).where(BoxRequestItem.box_id == source["id"])
    ).all()
    assert len(target_items) == 2
    assert {item.lot for item in target_items} == {
        "Overwrite Source",
        "Overwrite Target",
    }
    event_data = next(data for kind, data in published if kind == "lot.merged")
    assert event_data["overwritten_archived_box_count"] == 1
    assert event_data["removed_box_ids"] == [target["id"]]
    assert "reason" not in event_data


@pytest.mark.parametrize(
    ("source_archived", "target_archived"),
    [(True, False), (True, True)],
)
def test_archived_overwrite_target_survives_and_preserves_request_history(
    client,
    session,
    make_user,
    source_archived,
    target_archived,
):
    operator = make_user(UserRole.operator)
    source_data = _box(client, "41", f"History Source {source_archived}")
    target_data = _box(client, "41", f"History Target {target_archived}")
    source_box = session.get(Box, source_data["id"])
    target_box = session.get(Box, target_data["id"])
    archived_at = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)
    source_box.archived_at = archived_at if source_archived else None
    target_box.archived_at = archived_at if target_archived else None

    historical_requests = []
    for status in (BoxRequestStatus.in_transit, BoxRequestStatus.completed):
        request = BoxRequest(
            direction=BoxRequestDirection.return_,
            warehouse_id=1,
            quantity=1,
            status=status,
            requester_user_id=operator.id,
            origin=BoxRequestOrigin.workflow,
        )
        session.add(request)
        session.flush()
        item = BoxRequestItem(
            request_id=request.id,
            position=1,
            box_id=source_box.id,
            lot_id=source_box.lot_id,
            lot=source_box.lot,
            box_number=source_box.box_number,
        )
        session.add(item)
        session.flush()
        historical_requests.append((request, item))

    completed_item = historical_requests[-1][1]
    discrepancy = BoxRequestDiscrepancy(
        request_id=historical_requests[-1][0].id,
        request_item_id=completed_item.id,
        box_id=source_box.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        notes="preserve this discrepancy",
        created_by_user_id=operator.id,
    )
    session.add(discrepancy)
    session.flush()
    photo = BoxRequestDiscrepancyPhoto(
        discrepancy_id=discrepancy.id,
        object_key=f"merge-history/{source_box.id}.jpg",
        original_filename="damage.jpg",
        content_type="image/jpeg",
        size_bytes=123,
        sha256="a" * 64,
        uploaded_by_user_id=operator.id,
    )
    session.add(photo)
    session.commit()

    source = session.get(Lot, source_data["lot_id"])
    target = session.get(Lot, target_data["lot_id"])
    candidate = _merge_candidate(session, source, target)
    assert candidate.merge_allowed_with_archived_overwrite is True
    collision = candidate.resolvable_archived_collisions[0]
    assert collision.survivor_box_id == target_box.id
    assert collision.removed_box_id == source_box.id

    response = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            "target_lot_id": target.id,
            "reason": "Preserve all archived collision history",
            "expected_source_version": source.version,
            "expected_target_version": target.version,
            "overwrite_archived_collisions": True,
            "expected_collision_signature": candidate.collision_signature,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["overwritten_archived_box_count"] == 1
    assert result["relinked_request_item_count"] >= 3
    assert result["relinked_discrepancy_count"] == 1
    session.expire_all()
    assert session.get(Box, source_box.id) is None
    assert session.get(Box, target_box.id) is not None
    for request, item in historical_requests:
        stored_request = session.get(BoxRequest, request.id)
        stored_item = session.get(BoxRequestItem, item.id)
        assert stored_request.status == request.status
        assert stored_item.box_id == target_box.id
        assert stored_item.lot_id == target.id
        assert stored_item.lot == source.name
    stored_discrepancy = session.get(BoxRequestDiscrepancy, discrepancy.id)
    stored_photo = session.get(BoxRequestDiscrepancyPhoto, photo.id)
    assert stored_discrepancy.box_id == target_box.id
    assert stored_discrepancy.notes == "preserve this discrepancy"
    assert stored_photo.discrepancy_id == discrepancy.id
    assert stored_photo.object_key == photo.object_key

    audit_events = session.scalars(
        select(LotEvent)
        .where(
            LotEvent.lot_id.in_((source.id, target.id)),
            LotEvent.event_type == LotEventType.merged,
        )
        .order_by(LotEvent.lot_id)
    ).all()
    assert len(audit_events) == 2
    for event in audit_events:
        metadata = event.event_metadata
        assert metadata["collision_signature"] == candidate.collision_signature
        assert metadata["actor_user_id"] is not None
        assert metadata["reason"] == "Preserve all archived collision history"
        assert metadata["removed_boxes"]["ids"] == [source_box.id]
        assert metadata["survivor_boxes"]["ids"] == [target_box.id]
        assert metadata["totals"]["relinked_discrepancy_count"] == 1
        snapshot = metadata["overwrite_collisions"][0]
        assert snapshot["removed"]["lot_side"] == "source"
        assert snapshot["survivor"]["lot_side"] == "target"
        assert snapshot["relinked_discrepancies"]["ids"] == [discrepancy.id]
        assert snapshot["deleted_box_events"]["count"] >= 1
        assert snapshot["deleted_box_event_snapshots"]
        assert all(
            "note" not in event_snapshot
            for event_snapshot in snapshot["deleted_box_event_snapshots"]
        )


def test_archived_overwrite_relinks_every_history_shape_without_collateral_deletes(
    client, session, make_user
):
    admin = make_user(UserRole.admin)
    source_data = _box(client, "45", "History Matrix Source")
    target_data = _box(client, "45", "History Matrix Target")
    sibling_data = _box(client, "46", "History Matrix Sibling")
    source_box = session.get(Box, source_data["id"])
    target_box = session.get(Box, target_data["id"])
    sibling_box = session.get(Box, sibling_data["id"])

    mixed_receipt = create_completed_receipt(
        session,
        user=admin,
        warehouse_id=1,
        boxes=[source_box, sibling_box],
        origin=BoxRequestOrigin.manual_entry,
        note="mixed receipt must survive merge",
    )
    history_requests: list[BoxRequest] = []
    history_items: list[BoxRequestItem] = []
    for status in (BoxRequestStatus.in_transit, BoxRequestStatus.completed):
        request = BoxRequest(
            direction=BoxRequestDirection.return_,
            warehouse_id=1,
            quantity=1,
            status=status,
            requester_user_id=admin.id,
            origin=BoxRequestOrigin.workflow,
        )
        session.add(request)
        session.flush()
        request.root_request_id = request.id
        item = BoxRequestItem(
            request_id=request.id,
            position=1,
            box_id=source_box.id,
            lot_id=source_box.lot_id,
            lot=source_box.lot,
            box_number=source_box.box_number,
        )
        session.add(item)
        session.flush()
        history_requests.append(request)
        history_items.append(item)

    follow_up = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.draft,
        requester_user_id=admin.id,
        source_inbound_request_id=mixed_receipt.id,
        parent_request_id=history_requests[0].id,
        root_request_id=history_requests[0].id,
        origin=BoxRequestOrigin.return_reselection,
    )
    session.add(follow_up)
    session.flush()
    follow_up_item = BoxRequestItem(
        request_id=follow_up.id,
        position=1,
        box_id=source_box.id,
        lot_id=source_box.lot_id,
        lot=source_box.lot,
        box_number=source_box.box_number,
    )
    session.add(follow_up_item)
    session.flush()

    discrepancy = BoxRequestDiscrepancy(
        request_id=history_requests[-1].id,
        request_item_id=history_items[-1].id,
        box_id=source_box.id,
        discrepancy_type=BoxRequestDiscrepancyType.damaged,
        notes="history matrix discrepancy",
        created_by_user_id=admin.id,
    )
    session.add(discrepancy)
    session.flush()
    photo = BoxRequestDiscrepancyPhoto(
        discrepancy_id=discrepancy.id,
        object_key="merge-history/matrix-damage.jpg",
        original_filename="matrix-damage.jpg",
        content_type="image/jpeg",
        size_bytes=321,
        sha256="b" * 64,
        uploaded_by_user_id=admin.id,
    )
    document = BoxRequestDocument(
        request_id=mixed_receipt.id,
        document_type=BoxRequestDocumentType.delivery_note,
        erp_reference="MATRIX-ERP",
        object_key="merge-history/matrix-delivery.pdf",
        original_filename="matrix-delivery.pdf",
        content_type="application/pdf",
        size_bytes=654,
        sha256="c" * 64,
        uploaded_by_user_id=admin.id,
    )
    notification = InAppNotification(
        user_id=admin.id,
        request_id=mixed_receipt.id,
        warehouse_id=1,
        kind="merge_history_matrix",
        title="Preserve notification",
        body="This request notification must survive the merge.",
        deep_link=f"/requests/{mixed_receipt.id}",
        idempotency_key=f"merge-history-matrix:{mixed_receipt.id}",
    )
    purge_ledger = LotPurgeEvent(
        lot_id=999_999,
        lot_name="Previously Purged Unrelated Lot",
        lot_version=1,
        actor_user_id=admin.id,
        reason="Unrelated durable purge ledger",
        receipt_ids=[],
        receipt_count=0,
        archived_box_ids=[],
        archived_box_count=0,
        object_keys=[],
        object_key_count=0,
        object_cleanup_status=LotPurgeCleanupStatus.not_required,
        object_cleanup_failures=[],
        event_metadata={"scope": "unrelated"},
    )
    session.add_all([photo, document, notification, purge_ledger])
    source_box.archived_at = datetime.now(UTC)
    session.commit()

    source = session.get(Lot, source_data["lot_id"])
    target = session.get(Lot, target_data["lot_id"])
    candidate = _merge_candidate(session, source, target)
    request_ids_before = set(session.scalars(select(BoxRequest.id)).all())
    source_item_ids = set(
        session.scalars(
            select(BoxRequestItem.id).where(BoxRequestItem.box_id == source_box.id)
        ).all()
    )
    source_snapshot_by_item = {
        item_id: session.get(BoxRequestItem, item_id).lot
        for item_id in source_item_ids
    }
    sibling_item = next(
        item for item in mixed_receipt.items if item.box_id == sibling_box.id
    )
    sibling_event_count = int(
        session.scalar(
            select(func.count(BoxEvent.id)).where(BoxEvent.box_id == sibling_box.id)
        )
        or 0
    )

    response = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            "target_lot_id": target.id,
            "reason": "Relink every historical reference without collateral deletion",
            "expected_source_version": source.version,
            "expected_target_version": target.version,
            "overwrite_archived_collisions": True,
            "expected_collision_signature": candidate.collision_signature,
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["relinked_request_item_count"] == len(source_item_ids)
    assert result["relinked_discrepancy_count"] == 1
    session.expire_all()
    assert session.get(Box, source_box.id) is None
    assert session.get(Box, target_box.id).archived_at is None
    assert session.get(Box, sibling_box.id) is not None
    assert set(session.scalars(select(BoxRequest.id)).all()) == request_ids_before
    for item_id in source_item_ids:
        item = session.get(BoxRequestItem, item_id)
        assert item.box_id == target_box.id
        assert item.lot_id == target.id
        assert item.lot == source_snapshot_by_item[item_id]
    stored_sibling_item = session.get(BoxRequestItem, sibling_item.id)
    assert stored_sibling_item.box_id == sibling_box.id
    assert stored_sibling_item.lot_id == sibling_box.lot_id
    assert session.get(BoxRequest, follow_up.id).parent_request_id == history_requests[0].id
    assert session.get(BoxRequestDiscrepancy, discrepancy.id).box_id == target_box.id
    assert session.get(BoxRequestDiscrepancyPhoto, photo.id).object_key == photo.object_key
    assert session.get(BoxRequestDocument, document.id).object_key == document.object_key
    assert session.get(InAppNotification, notification.id).request_id == mixed_receipt.id
    assert session.get(LotPurgeEvent, purge_ledger.id).lot_id == 999_999
    assert (
        int(
            session.scalar(
                select(func.count(BoxEvent.id)).where(
                    BoxEvent.box_id == sibling_box.id
                )
            )
            or 0
        )
        == sibling_event_count
    )


def test_overwrite_missing_ack_and_active_active_remain_atomic(client, session):
    source_data = _box(client, "51", "Atomic Source")
    target_data = _box(client, "51", "Atomic Target")
    target_box = session.get(Box, target_data["id"])
    target_box.archived_at = datetime.now(UTC)
    session.commit()
    source = session.get(Lot, source_data["lot_id"])
    target = session.get(Lot, target_data["lot_id"])
    base = {
        "target_lot_id": target.id,
        "reason": "Must retain atomic collision state",
        "expected_source_version": source.version,
        "expected_target_version": target.version,
    }

    no_ack = client.post(f"/api/lots/{source.id}/merge", json=base)
    assert no_ack.status_code == 409
    assert no_ack.json()["detail"]["code"] == "box_number_overlap"
    missing_signature = client.post(
        f"/api/lots/{source.id}/merge",
        json={**base, "overwrite_archived_collisions": True},
    )
    assert missing_signature.status_code == 409
    assert missing_signature.json()["detail"]["code"] == "overlap_signature_mismatch"

    target_box = session.get(Box, target_data["id"])
    target_box.archived_at = None
    session.commit()
    source = session.get(Lot, source.id)
    target = session.get(Lot, target.id)
    active_candidate = _merge_candidate(session, source, target)
    active_active = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            **base,
            "expected_source_version": source.version,
            "expected_target_version": target.version,
            "overwrite_archived_collisions": True,
            "expected_collision_signature": active_candidate.collision_signature,
        },
    )
    assert active_active.status_code == 409
    assert active_active.json()["detail"]["code"] == "box_number_overlap"
    session.expire_all()
    assert session.get(Box, source_data["id"]).lot_id == source.id
    assert session.get(Box, target_data["id"]).lot_id == target.id
    assert session.get(Lot, source.id).merged_into_lot_id is None


def test_overwrite_rejects_link_graph_drift_with_refreshed_candidate(
    client, session, make_user
):
    operator = make_user(UserRole.operator)
    source_data = _box(client, "61", "Drift Source")
    target_data = _box(client, "61", "Drift Target")
    target_box = session.get(Box, target_data["id"])
    target_box.archived_at = datetime.now(UTC)
    session.commit()
    source = session.get(Lot, source_data["lot_id"])
    target = session.get(Lot, target_data["lot_id"])
    candidate = _merge_candidate(session, source, target)
    before_count = candidate.resolvable_archived_collisions[
        0
    ].request_item_relink_count

    request = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.in_transit,
        requester_user_id=operator.id,
        origin=BoxRequestOrigin.workflow,
    )
    session.add(request)
    session.flush()
    session.add(
        BoxRequestItem(
            request_id=request.id,
            position=1,
            box_id=target_box.id,
            lot_id=target.id,
            lot=target.name,
            box_number=target_box.box_number,
        )
    )
    session.commit()

    response = client.post(
        f"/api/lots/{source.id}/merge",
        json={
            "target_lot_id": target.id,
            "reason": "Reject stale collision link graph",
            "expected_source_version": source.version,
            "expected_target_version": target.version,
            "overwrite_archived_collisions": True,
            "expected_collision_signature": candidate.collision_signature,
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "overlap_signature_mismatch"
    refreshed = detail["merge_candidate"]
    assert refreshed["collision_signature"] != candidate.collision_signature
    assert (
        refreshed["resolvable_archived_collisions"][0][
            "request_item_relink_count"
        ]
        == before_count + 1
    )
    session.expire_all()
    assert session.get(Box, target_box.id) is not None
    assert session.get(Lot, source.id).merged_into_lot_id is None


def test_overwrite_rolls_back_every_mutation_on_injected_failure(
    session, make_user, monkeypatch
):
    admin = make_user(UserRole.admin)
    source_box = create_box(
        session,
        user=admin,
        box_number="71",
        lot="Rollback Source",
        warehouse_id=1,
    )
    target_box = create_box(
        session,
        user=admin,
        box_number="71",
        lot="Rollback Target",
        warehouse_id=1,
    )
    receipt = create_completed_receipt(
        session,
        user=admin,
        warehouse_id=1,
        boxes=[source_box, target_box],
        origin=BoxRequestOrigin.manual_entry,
        note="rollback overwrite",
    )
    target_box.archived_at = datetime.now(UTC)
    session.commit()
    source = source_box.lot_record
    target = target_box.lot_record
    candidate = _merge_candidate(session, source, target)
    item_ids_by_box = {item.box_id: item.id for item in receipt.items}
    original_flush = session.flush
    calls = 0

    def fail_during_overwrite(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected overwrite failure")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_during_overwrite)
    with pytest.raises(RuntimeError, match="injected overwrite failure"):
        merge_lots(
            session,
            user=admin,
            source_lot_id=source.id,
            target_lot_id=target.id,
            reason="Exercise all-or-nothing overwrite rollback",
            expected_source_version=source.version,
            expected_target_version=target.version,
            overwrite_archived_collisions=True,
            expected_collision_signature=candidate.collision_signature,
        )

    session.expire_all()
    assert session.get(Box, source_box.id).lot_id == source.id
    assert session.get(Box, target_box.id).lot_id == target.id
    assert session.get(BoxRequestItem, item_ids_by_box[source_box.id]).box_id == source_box.id
    assert session.get(BoxRequestItem, item_ids_by_box[target_box.id]).box_id == target_box.id
    assert session.get(Lot, source.id).merged_into_lot_id is None
    assert session.scalar(
        select(LotEvent).where(
            LotEvent.lot_id == source.id,
            LotEvent.event_type == LotEventType.merged,
        )
    ) is None


def test_lot_collision_contract_is_present_in_openapi(client):
    schema = client.get("/api/openapi.json").json()
    components = schema["components"]["schemas"]
    assert {
        "LotArchivedBoxCollisionOut",
        "LotHardBoxOverlapOut",
        "LotMergeCandidateOut",
        "LotMergeConflictResponse",
        "LotRenameConflictResponse",
    } <= components.keys()
    merge_schema = components["LotMerge"]["properties"]
    assert merge_schema["overwrite_archived_collisions"]["default"] is False
    assert merge_schema["expected_collision_signature"]["anyOf"][0][
        "minLength"
    ] == 64


def test_merge_requires_admin_and_nonblank_reason(client, session, make_user):
    source = _box(client, "1", "Auth Source")
    target = _box(client, "2", "Auth Target")
    source_lot = session.get(Lot, source["lot_id"])
    target_lot = session.get(Lot, target["lot_id"])
    operator = make_user(UserRole.operator)
    app.dependency_overrides[get_current_user] = lambda: operator
    payload = {
        "target_lot_id": target_lot.id,
        "reason": "denied",
        "expected_source_version": source_lot.version,
        "expected_target_version": target_lot.version,
    }
    assert client.post(f"/api/lots/{source_lot.id}/merge", json=payload).status_code == 403

    admin = make_user(UserRole.admin)
    app.dependency_overrides[get_current_user] = lambda: admin
    payload["reason"] = "   "
    response = client.post(f"/api/lots/{source_lot.id}/merge", json=payload)
    assert response.status_code == 400
