from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from app.deps import get_current_user
from app.events import bus
from app.main import app
from app.models import (
    Box,
    BoxEvent,
    BoxEventType,
    BoxRequestItem,
    BoxRequestOrigin,
    Lot,
    LotEvent,
    LotEventType,
    UserRole,
)
from app.services.boxes import BoxRuleError, create_box
from app.services.lots import (
    _active_lot_use_statement,
    _exclusive_lot_statement,
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
    statement = (
        select(Box)
        .where(Box.lot_id.in_((3, 9)))
        .order_by(Box.id)
        .with_for_update(of=Box)
    )
    postgres_sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "LEFT OUTER JOIN lots" in postgres_sql
    assert postgres_sql.endswith("FOR UPDATE OF boxes")


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
