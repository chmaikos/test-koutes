from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import (
    Box,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
    BoxStatus,
    Lot,
    LotPurgeEvent,
    Pallet,
    PalletEvent,
    PalletEventType,
    UserRole,
)
from app.services.lots import (
    LotMergeConflictError,
    LotPurgeConflictError,
    _merge_candidate,
    _merge_pallet_event_lock_statement,
    _merge_pallet_lock_statement,
    _purge_pallet_lock_statement,
    analyze_lot_force_purge_impact,
    analyze_lot_purge_eligibility,
    force_purge_lot,
    merge_lots,
    purge_lot,
)


def _lot(session, name: str) -> Lot:
    lot = Lot(name=name)
    session.add(lot)
    session.flush()
    return lot


def _pallet(
    session,
    lot: Lot,
    number: str,
    *,
    active: bool = True,
) -> Pallet:
    pallet = Pallet(
        lot_id=lot.id,
        pallet_number=number,
        is_active=active,
        archived_at=None if active else datetime.now(UTC),
    )
    session.add(pallet)
    session.flush()
    session.add(
        PalletEvent(
            pallet_id=pallet.id,
            event_type=PalletEventType.created,
            new_pallet_number=pallet.pallet_number,
        )
    )
    return pallet


def _box(
    session,
    lot: Lot,
    pallet: Pallet,
    number: str,
    *,
    archived: bool = False,
    warehouse_id: int = 1,
) -> Box:
    box = Box(
        box_number=number,
        lot_id=lot.id,
        pallet_id=pallet.id,
        current_warehouse_id=warehouse_id,
        status=BoxStatus.received,
        archived_at=datetime.now(UTC) if archived else None,
    )
    session.add(box)
    session.flush()
    return box


def _request(session, user, boxes: list[Box]) -> BoxRequest:
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=boxes[0].current_warehouse_id,
        quantity=len(boxes),
        actual_received_quantity=len(boxes),
        variance_quantity=0,
        status=BoxRequestStatus.completed,
        requester_user_id=user.id,
        origin=BoxRequestOrigin.manual_entry,
        completed_at=datetime.now(UTC),
    )
    session.add(request)
    session.flush()
    request.root_request_id = request.id
    session.add(
        BoxRequestEvent(
            request_id=request.id,
            event_type=BoxRequestEventType.completed,
            to_status=BoxRequestStatus.completed,
            user_id=user.id,
        )
    )
    for position, box in enumerate(boxes, start=1):
        session.add(
            BoxRequestItem(
                request_id=request.id,
                position=position,
                box_id=box.id,
                lot_id=box.lot_id,
                pallet_id=box.pallet_id,
                lot=box.lot,
                pallet=box.pallet.pallet_number,
                box_number=box.box_number,
            )
        )
    session.flush()
    return request


def test_merge_combines_same_number_same_warehouse_and_preserves_snapshot(
    session, make_user
) -> None:
    admin = make_user(UserRole.admin)
    source = _lot(session, "Pallet Merge Source")
    target = _lot(session, "Pallet Merge Target")
    source_pallet = _pallet(session, source, " Rack A ")
    target_pallet = _pallet(session, target, "RACK A")
    source_box = _box(session, source, source_pallet, "001")
    _box(session, target, target_pallet, "002")
    request = _request(session, admin, [source_box])
    source_snapshot = request.items[0].pallet
    source_barcode = source.barcode
    source_pallet_barcode = source_pallet.barcode
    session.commit()

    result = merge_lots(
        session,
        user=admin,
        source_lot_id=source.id,
        target_lot_id=target.id,
        reason="The supplier pallet identities are the same",
        expected_source_version=source.version,
        expected_target_version=target.version,
    )
    session.expire_all()

    assert result.combined_pallet_count == 1
    assert result.absorbed_pallet_ids == [source_pallet.id]
    assert session.get(Box, source_box.id).pallet_id == target_pallet.id
    item = session.get(BoxRequestItem, request.items[0].id)
    assert item.lot_id == target.id
    assert item.pallet_id == target_pallet.id
    assert item.pallet == source_snapshot
    absorbed = session.get(Pallet, source_pallet.id)
    assert absorbed.is_active is False
    assert absorbed.absorbed_into_pallet_id == target_pallet.id
    assert absorbed.barcode == source_pallet_barcode
    assert absorbed.barcode_identity.retired_at is None
    merged_source = session.get(Lot, source.id)
    assert merged_source.barcode == source_barcode
    assert merged_source.barcode_identity.retired_at is None
    assert session.scalar(
        select(PalletEvent.id).where(
            PalletEvent.pallet_id == source_pallet.id,
            PalletEvent.event_type == PalletEventType.merged_absorbed,
        )
    )


def test_merge_combines_same_number_across_warehouses(
    session, make_user
) -> None:
    admin = make_user(UserRole.admin)
    source = _lot(session, "Warehouse Collision Source")
    target = _lot(session, "Warehouse Collision Target")
    source_pallet = _pallet(session, source, "Shared")
    target_pallet = _pallet(session, target, "shared")
    source_box = _box(
        session, source, source_pallet, "001", warehouse_id=1
    )
    _box(session, target, target_pallet, "002", warehouse_id=2)
    session.commit()

    candidate = _merge_candidate(session, source, target)
    assert candidate.merge_allowed is True
    assert candidate.pallet_collisions == []
    assert candidate.pallet_actions[0].action == "combine"

    result = merge_lots(
        session,
        user=admin,
        source_lot_id=source.id,
        target_lot_id=target.id,
        reason="Combine one organizational pallet identity",
        expected_source_version=source.version,
        expected_target_version=target.version,
    )
    session.expire_all()
    assert result.combined_pallet_count == 1
    assert session.get(Box, source_box.id).pallet_id == target_pallet.id


def test_merge_transfers_nonmatching_archived_pallet_and_signature_tracks_topology(
    session, make_user
) -> None:
    admin = make_user(UserRole.admin)
    source = _lot(session, "Transfer Source")
    target = _lot(session, "Transfer Target")
    source_pallet = _pallet(session, source, "Unique", active=False)
    source_box = _box(session, source, source_pallet, "001", archived=True)
    session.commit()

    before = _merge_candidate(session, source, target)
    assert before.pallet_actions[0].action == "transfer"
    _pallet(session, target, "Unrelated")
    session.flush()
    after = _merge_candidate(session, source, target)
    assert after.collision_signature != before.collision_signature
    session.commit()

    with pytest.raises(LotMergeConflictError) as stale:
        merge_lots(
            session,
            user=admin,
            source_lot_id=source.id,
            target_lot_id=target.id,
            reason="Reject stale pallet topology confirmation",
            expected_source_version=source.version,
            expected_target_version=target.version,
            overwrite_archived_collisions=True,
            expected_collision_signature=before.collision_signature,
        )
    assert stale.value.code == "overlap_signature_mismatch"
    session.rollback()

    result = merge_lots(
        session,
        user=admin,
        source_lot_id=source.id,
        target_lot_id=target.id,
        reason="Transfer a nonmatching archived pallet intact",
        expected_source_version=source.version,
        expected_target_version=target.version,
    )
    session.expire_all()
    assert result.moved_pallet_ids == [source_pallet.id]
    transferred = session.get(Pallet, source_pallet.id)
    assert transferred.lot_id == target.id
    assert transferred.is_active is False
    assert session.get(Box, source_box.id).pallet_id == transferred.id
    assert session.scalar(
        select(PalletEvent.id).where(
            PalletEvent.pallet_id == transferred.id,
            PalletEvent.event_type == PalletEventType.lot_reassigned,
        )
    )


def test_archived_box_overwrite_remaps_survivor_to_combined_target_pallet(
    session, make_user
) -> None:
    admin = make_user(UserRole.admin)
    source = _lot(session, "Overlap Pallet Source")
    target = _lot(session, "Overlap Pallet Target")
    source_pallet = _pallet(session, source, "Combined")
    target_pallet = _pallet(session, target, "combined")
    survivor = _box(session, source, source_pallet, "001")
    removed = _box(session, target, target_pallet, "001", archived=True)
    request = _request(session, admin, [removed])
    snapshot = request.items[0].pallet
    session.commit()
    candidate = _merge_candidate(session, source, target)

    result = merge_lots(
        session,
        user=admin,
        source_lot_id=source.id,
        target_lot_id=target.id,
        reason="Resolve archived overlap while combining matching pallets",
        expected_source_version=source.version,
        expected_target_version=target.version,
        overwrite_archived_collisions=True,
        expected_collision_signature=candidate.collision_signature,
    )
    session.expire_all()

    assert result.overwritten_archived_box_count == 1
    assert session.get(Box, removed.id) is None
    assert session.get(Box, survivor.id).pallet_id == target_pallet.id
    item = session.get(BoxRequestItem, request.items[0].id)
    assert item.box_id == survivor.id
    assert item.pallet_id == target_pallet.id
    assert item.pallet == snapshot


def test_merge_pallet_and_purge_locks_are_explicit_and_ordered() -> None:
    for statement, table, order_column in (
        (_merge_pallet_lock_statement([9, 3]), "pallets", "pallets.id"),
        (
            _merge_pallet_event_lock_statement([9, 3]),
            "pallet_events",
            "pallet_events.id",
        ),
        (_purge_pallet_lock_statement(9), "pallets", "pallets.id"),
    ):
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert f"ORDER BY {order_column}" in sql
        assert sql.endswith(f"FOR UPDATE OF {table}")


def test_safe_purge_deletes_selected_pallet_events_and_snapshots_them(
    session, make_user
) -> None:
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    lot = _lot(session, "Safe Pallet Purge")
    pallet = _pallet(session, lot, "Delete Me")
    box = _box(session, lot, pallet, "001", archived=True)
    request = _request(session, operator, [box])
    pallet_id = pallet.id
    request_id = request.id
    session.commit()

    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)
    assert preview.eligible
    assert preview.active_pallet_ids == [pallet_id]
    result = purge_lot(
        session,
        user=admin,
        lot_id=lot.id,
        confirmation_name=lot.name,
        reason="Delete the duplicate self receipt and its pallet",
        expected_version=lot.version,
        expected_graph_signature=preview.graph_signature,
    )
    session.expire_all()

    assert result.pallet_count == 1
    assert session.get(Pallet, pallet_id) is None
    assert session.scalar(
        select(PalletEvent.id).where(PalletEvent.pallet_id == pallet_id)
    ) is None
    audit = session.get(LotPurgeEvent, result.audit_id)
    assert audit.pallet_ids == [pallet_id]
    assert audit.pallet_count == 1
    assert audit.pallet_snapshots[0]["pallet_number"] == "Delete Me"
    assert audit.pallet_snapshots[0]["assigned_box_count"] == 1
    assert audit.pallet_snapshots[0]["active_box_count"] == 0
    assert audit.pallet_snapshots[0]["archived_box_count"] == 1
    assert audit.pallet_snapshots[0]["warehouse_ids"] == [1]
    assert audit.event_metadata["deleted_counts"]["pallet_events"] == 1
    assert session.get(BoxRequest, request_id) is None


def test_safe_purge_signature_rejects_changed_box_pallet_topology(
    session, make_user
) -> None:
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    lot = _lot(session, "Pallet Topology Signature")
    pallet = _pallet(session, lot, "Topology")
    box = _box(session, lot, pallet, "001", archived=True)
    _request(session, operator, [box])
    session.commit()

    preview = analyze_lot_purge_eligibility(session, lot_id=lot.id)
    box.pallet_id = None
    session.commit()
    changed = analyze_lot_purge_eligibility(session, lot_id=lot.id)
    assert changed.graph_signature != preview.graph_signature

    with pytest.raises(LotPurgeConflictError) as stale:
        purge_lot(
            session,
            user=admin,
            lot_id=lot.id,
            confirmation_name=lot.name,
            reason="Reject the changed pallet assignment graph",
            expected_version=lot.version,
            expected_graph_signature=preview.graph_signature,
        )
    assert stale.value.code == "graph_changed"
    session.rollback()
    assert session.get(Lot, lot.id) is not None
    assert session.get(Pallet, pallet.id) is not None
    assert session.get(Box, box.id) is not None


def test_force_purge_clears_live_pallet_fk_but_preserves_mixed_snapshot_and_target(
    session, make_user
) -> None:
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    selected_lot = _lot(session, "Force Pallet Selected")
    sibling_lot = _lot(session, "Force Pallet Sibling")
    selected_pallet = _pallet(session, selected_lot, "Selected")
    sibling_pallet = _pallet(session, sibling_lot, "Sibling")
    selected_box = _box(session, selected_lot, selected_pallet, "001")
    sibling_box = _box(session, sibling_lot, sibling_pallet, "001")
    request = _request(session, operator, [selected_box, sibling_box])
    sibling_item = next(item for item in request.items if item.box_id == sibling_box.id)
    snapshot = sibling_item.pallet
    # Simulate a legacy mixed-row live FK into the selected lot. Force purge
    # must clear only this FK while retaining the immutable display snapshot.
    sibling_item.pallet_id = selected_pallet.id
    selected_pallet_id = selected_pallet.id
    session.commit()

    preview = analyze_lot_force_purge_impact(session, lot_id=selected_lot.id)
    result = force_purge_lot(
        session,
        user=admin,
        lot_id=selected_lot.id,
        confirmation_name=preview.lot_name,
        confirmation_phrase=preview.confirmation_phrase,
        reason="Remove only the selected lot pallet and preserve sibling history",
        expected_version=preview.lot_version,
        expected_graph_signature=preview.graph_signature,
        acknowledged_blocker_codes=[
            blocker.code for blocker in preview.overridden_blockers
        ],
    )
    session.expire_all()

    assert result.pallet_count == 1
    assert session.get(Pallet, selected_pallet_id) is None
    assert session.get(Pallet, sibling_pallet.id) is not None
    preserved = session.get(BoxRequestItem, sibling_item.id)
    assert preserved.pallet_id is None
    assert preserved.pallet == snapshot
    audit = session.get(LotPurgeEvent, result.audit_id)
    assert audit.pallet_ids == [selected_pallet_id]
    assert audit.pallet_snapshots[0]["assigned_box_count"] == 1
    assert audit.pallet_snapshots[0]["active_box_count"] == 1
    assert audit.pallet_snapshots[0]["archived_box_count"] == 0
    assert audit.pallet_snapshots[0]["warehouse_ids"] == [1]
    assert audit.event_metadata["deleted_counts"]["pallets"] == 1
