from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.deps import get_current_user
from app.main import app
from app.models import (
    Box,
    BoxEvent,
    BoxEventType,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestEvent,
    BoxRequestEventType,
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
from app.schemas.requests import InboundBoxItem, InboundCompletionPreviewOut
from app.services.boxes import BoxRuleError
from app.services.requests import merge_inbound_items, preview_inbound_completion


def _as_user(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _request(
    session,
    requester,
    *,
    warehouse_id: int = 1,
    direction: BoxRequestDirection = BoxRequestDirection.inbound,
    status: BoxRequestStatus = BoxRequestStatus.awaiting_confirmation,
    quantity: int = 10,
) -> BoxRequest:
    request = BoxRequest(
        direction=direction,
        warehouse_id=warehouse_id,
        target_warehouse_id=2 if direction == BoxRequestDirection.return_ else None,
        quantity=quantity,
        status=status,
        requester_user_id=requester.id,
    )
    session.add(request)
    session.flush()
    return request


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
    warehouse_id: int = 1,
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
    return pallet


def _box(
    session,
    lot: Lot,
    number: str,
    *,
    warehouse_id: int,
    status: BoxStatus = BoxStatus.received,
    pallet: Pallet | None = None,
    archived: bool = False,
) -> Box:
    box = Box(
        lot_id=lot.id,
        box_number=number.zfill(3),
        current_warehouse_id=warehouse_id,
        status=status,
        pallet_id=pallet.id if pallet is not None else None,
        archived_at=datetime.now(UTC) if archived else None,
    )
    session.add(box)
    session.flush()
    return box


def _preview(client, request_id: int, rows: list[dict[str, object]]):
    return client.post(
        f"/api/requests/{request_id}/inbound-completion-preview",
        json={"inbound_items": rows},
    )


def _complete(
    client,
    request: BoxRequest,
    rows: list[dict[str, object]],
    *,
    idempotency_key: str,
    signature: str | None = None,
    accept: bool = False,
):
    return client.post(
        f"/api/requests/{request.id}/complete",
        json={
            "expected_version": request.version,
            "idempotency_key": idempotency_key,
            "inbound_items": rows,
            "accept_existing_received_boxes": accept,
            "inbound_impact_signature": signature,
        },
    )


def _counts(session) -> dict[str, int]:
    return {
        model.__tablename__: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for model in (
            Box,
            BoxEvent,
            BoxRequest,
            BoxRequestEvent,
            BoxRequestItem,
            Lot,
            Pallet,
            PalletEvent,
        )
    }


def test_preview_classifies_all_box_outcomes_with_target_only_acl_and_no_mutation(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = _lot(session, "Existing")
    source_pallet = _pallet(session, lot, "Source", warehouse_id=2)
    relocatable = _box(
        session,
        lot,
        "1",
        warehouse_id=2,
        pallet=source_pallet,
    )
    at_target = _box(session, lot, "2", warehouse_id=1)
    archived = _box(session, lot, "3", warehouse_id=2, archived=True)
    invalid = _box(
        session,
        lot,
        "4",
        warehouse_id=2,
        status=BoxStatus.processing,
    )
    request = _request(session, requester)
    session.commit()
    before = _counts(session)
    _as_user(requester)

    response = _preview(
        client,
        request.id,
        [
            {"lot": "New Lot", "box_number": "1", "pallet_number": "New"},
            {"lot": "existing", "box_number": "1", "pallet_number": "Target A"},
            {"lot": "Existing", "box_number": "2", "pallet_number": "Target B"},
            {"lot": "Existing", "box_number": "3", "pallet_number": "Target C"},
            {"lot": "Existing", "box_number": "4", "pallet_number": "Target D"},
        ],
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"] == {
        "created": 1,
        "relocated": 1,
        "blocked": 3,
        "source_warehouse_counts": [
            {"warehouse_id": 2, "warehouse_name": "Building 2", "count": 1}
        ],
    }
    assert payload["can_complete"] is False
    by_number = {
        (row["normalized_lot"], row["normalized_box_number"]): row
        for row in payload["rows"]
    }
    assert by_number[("new lot", "001")]["classification"] == "create"
    assert by_number[("existing", "001")] == {
        **by_number[("existing", "001")],
        "classification": "relocate",
        "existing_box_id": relocatable.id,
        "current_status": "received",
        "source_warehouse_id": 2,
        "source_warehouse_name": "Building 2",
        "current_pallet_id": source_pallet.id,
        "current_pallet_number": source_pallet.pallet_number,
        "blocked_code": None,
        "blocked_message": None,
    }
    assert by_number[("existing", "002")]["blocked_code"] == "existing_at_target"
    assert by_number[("existing", "002")]["existing_box_id"] == at_target.id
    assert by_number[("existing", "003")]["blocked_code"] == "archived_identity"
    assert by_number[("existing", "003")]["existing_box_id"] == archived.id
    assert by_number[("existing", "004")]["blocked_code"] == "invalid_status"
    assert by_number[("existing", "004")]["existing_box_id"] == invalid.id
    assert _counts(session) == before
    assert session.get(BoxRequest, request.id).status == BoxRequestStatus.awaiting_confirmation


def test_preview_permissions_direction_and_lifecycle(client, session, make_user) -> None:
    requester = make_user(UserRole.viewer)
    other = make_user(UserRole.viewer)
    admin = make_user(UserRole.admin)
    request = _request(session, requester)
    early = _request(session, requester, status=BoxRequestStatus.in_transit)
    return_request = _request(
        session,
        requester,
        direction=BoxRequestDirection.return_,
    )
    session.commit()
    rows = [{"lot": "Lot", "box_number": "1", "pallet_number": "P"}]

    _as_user(other)
    assert _preview(client, request.id, rows).status_code == 403
    requester.warehouses = [session.get(Warehouse, 2)]
    session.commit()
    _as_user(requester)
    assert _preview(client, request.id, rows).status_code == 404

    _as_user(admin)
    assert _preview(client, request.id, rows).status_code == 200
    assert _preview(client, early.id, rows).status_code == 409
    assert _preview(client, return_request.id, rows).status_code == 409


def test_preview_signature_is_order_independent_after_duplicate_merge(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    request = _request(session, requester)
    session.commit()
    _as_user(requester)
    first_rows = [
        {
            "lot": " Stable Lot ",
            "box_number": "1",
            "pallet_number": "Pallet A",
            "contents": "Beta",
        },
        {
            "lot": "stable lot",
            "box_number": "001",
            "pallet_number": "pallet a",
            "contents": "Alpha",
        },
        {
            "lot": "Other",
            "box_number": "2",
            "pallet_number": "Pallet B",
        },
    ]
    first = _preview(client, request.id, first_rows)
    second = _preview(client, request.id, list(reversed(first_rows)))

    assert first.status_code == second.status_code == 200
    assert first.json()["impact_signature"] == second.json()["impact_signature"]
    assert len(first.json()["rows"]) == 2
    conflict = _preview(
        client,
        request.id,
        [
            {"lot": "Stable Lot", "box_number": "1", "pallet_number": "A"},
            {"lot": "stable lot", "box_number": "001", "pallet_number": "B"},
        ],
    )
    assert conflict.status_code == 400
    assert "different pallet values" in conflict.json()["detail"]


def test_duplicate_merge_result_is_canonical_across_row_order() -> None:
    rows = [
        InboundBoxItem(
            lot="stable lot",
            box_number="1",
            pallet_number="target",
            contents="Beta",
        ),
        InboundBoxItem(
            lot="Stable Lot",
            box_number="001",
            pallet_number="TARGET",
            contents="Alpha",
        ),
    ]

    first = merge_inbound_items(rows)
    second = merge_inbound_items(list(reversed(rows)))

    assert first == second
    assert first[0].model_dump() == {
        "lot": "Stable Lot",
        "box_number": "001",
        "pallet_number": "TARGET",
        "pallet_id": None,
        "contents": "Alpha | Beta",
    }


def test_preview_disables_autoflush_for_pending_lot_and_pallet(
    session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    request = _request(session, requester)
    existing_lot = _lot(session, "Existing")
    session.commit()
    pending_lot = Lot(name="Must Not Flush")
    pending_pallet = Pallet(
        lot_id=existing_lot.id,
        pallet_number="Must Not Flush",
    )
    session.add_all((pending_lot, pending_pallet))

    preview = preview_inbound_completion(
        session,
        request_id=request.id,
        user=requester,
        inbound_items=[
            InboundBoxItem(
                lot="Preview Only",
                box_number="1",
                pallet_number="Preview Only",
            )
        ],
    )

    assert preview.summary.created == 1
    assert pending_lot.id is None
    assert pending_pallet.id is None
    session.rollback()


def test_preview_blocks_active_return_reservations_and_reports_ids(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Reserved")
    box = _box(session, lot, "1", warehouse_id=2)
    reservation = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=2,
        target_warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
        requester_user_id=requester.id,
    )
    session.add(reservation)
    session.flush()
    session.add(
        BoxRequestItem(
            request_id=reservation.id,
            position=1,
            box_id=box.id,
            lot_id=lot.id,
            lot=lot.name,
            box_number=box.box_number,
        )
    )
    request = _request(session, requester)
    session.commit()
    _as_user(requester)

    response = _preview(
        client,
        request.id,
        [{"lot": "Reserved", "box_number": "1", "pallet_number": "Target"}],
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["classification"] == "blocked"
    assert row["blocked_code"] == "active_return_reservation"
    assert row["active_return_reservation_ids"] == [reservation.id]


def test_preview_validates_target_pallets_without_creating_them(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    existing_lot = _lot(session, "Existing Pallet")
    existing = _pallet(session, existing_lot, "Target Existing")
    archived_lot = _lot(session, "Archived Pallet")
    archived = _pallet(session, archived_lot, "Target Archived", active=False)
    remote_lot = _lot(session, "Remote Pallet")
    remote = _pallet(session, remote_lot, "Target Remote", warehouse_id=2)
    mismatch_lot = _lot(session, "Mismatch Pallet")
    mismatch = _pallet(session, mismatch_lot, "Right Number")
    other_lot = _lot(session, "Other Lot")
    other = _pallet(session, other_lot, "Other")
    request = _request(session, requester)
    session.commit()
    pallet_count = session.scalar(select(func.count(Pallet.id)))
    _as_user(requester)

    response = _preview(
        client,
        request.id,
        [
            {
                "lot": existing_lot.name,
                "box_number": "1",
                "pallet_number": existing.pallet_number,
            },
            {"lot": "Brand New", "box_number": "1", "pallet_number": "Will Create"},
            {
                "lot": archived_lot.name,
                "box_number": "1",
                "pallet_number": archived.pallet_number,
            },
            {
                "lot": remote_lot.name,
                "box_number": "1",
                "pallet_number": remote.pallet_number,
            },
            {
                "lot": mismatch_lot.name,
                "box_number": "1",
                "pallet_number": "Wrong Number",
                "pallet_id": mismatch.id,
            },
            {
                "lot": mismatch_lot.name,
                "box_number": "2",
                "pallet_number": other.pallet_number,
                "pallet_id": other.id,
            },
            {
                "lot": mismatch_lot.name,
                "box_number": "3",
                "pallet_number": "Missing",
                "pallet_id": 999999,
            },
        ],
    )

    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    by_lot_box = {
        (row["normalized_lot"], row["normalized_box_number"]): row for row in rows
    }
    resolved = by_lot_box[("existing pallet", "001")]
    assert resolved["classification"] == "create"
    assert resolved["target_pallet_resolution"] == {
        "resolution": "existing",
        "pallet_id": existing.id,
        "pallet_number": existing.pallet_number,
    }
    assert by_lot_box[("brand new", "001")]["target_pallet_resolution"][
        "resolution"
    ] == "will_create"
    assert by_lot_box[("archived pallet", "001")]["blocked_code"] == (
        "target_pallet_archived"
    )
    remote_row = by_lot_box[("remote pallet", "001")]
    assert remote_row["blocked_code"] is None
    assert remote_row["target_pallet_resolution"]["resolution"] == "existing"
    assert by_lot_box[("mismatch pallet", "001")]["blocked_code"] == (
        "target_pallet_identity_mismatch"
    )
    assert by_lot_box[("mismatch pallet", "002")]["blocked_code"] == (
        "target_pallet_lot_mismatch"
    )
    assert by_lot_box[("mismatch pallet", "003")]["blocked_code"] == (
        "target_pallet_not_found"
    )
    assert session.scalar(select(func.count(Pallet.id))) == pallet_count


def test_preview_reports_unassigned_and_preserve_existing_targets(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Optional Pallets")
    pallet = _pallet(session, lot, "Keep Me")
    assigned = _box(session, lot, "1", warehouse_id=2, pallet=pallet)
    unassigned = _box(session, lot, "2", warehouse_id=2)
    request = _request(session, requester, quantity=3)
    session.commit()
    _as_user(requester)

    response = _preview(
        client,
        request.id,
        [
            {"lot": "New Optional Lot", "box_number": "1"},
            {"lot": lot.name, "box_number": assigned.box_number},
            {"lot": lot.name, "box_number": unassigned.box_number},
        ],
    )

    assert response.status_code == 200, response.text
    validated = InboundCompletionPreviewOut.model_validate(response.json())
    rows = {
        (row.normalized_lot, row.normalized_box_number): row
        for row in validated.rows
    }
    assert rows[("new optional lot", "001")].target_pallet_resolution.model_dump() == {
        "resolution": "unassigned",
        "pallet_id": None,
        "pallet_number": None,
    }
    assert rows[("optional pallets", "001")].target_pallet_resolution.model_dump() == {
        "resolution": "preserve_existing",
        "pallet_id": pallet.id,
        "pallet_number": pallet.pallet_number,
    }
    assert rows[("optional pallets", "002")].target_pallet_resolution.model_dump() == {
        "resolution": "unassigned",
        "pallet_id": None,
        "pallet_number": None,
    }


def test_completion_mixes_unassigned_creation_and_preserved_relocations(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = _lot(session, "Optional Completion")
    pallet = _pallet(session, lot, "Preserved")
    assigned = _box(session, lot, "1", warehouse_id=2, pallet=pallet)
    unassigned = _box(session, lot, "2", warehouse_id=3)
    request = _request(session, requester, quantity=3)
    session.commit()
    rows = [
        {"lot": lot.name, "box_number": assigned.box_number},
        {"lot": lot.name, "box_number": unassigned.box_number},
        {"lot": "New Unassigned", "box_number": "1"},
    ]
    _as_user(requester)

    preview = _preview(client, request.id, rows)
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"] == {
        "created": 1,
        "relocated": 2,
        "blocked": 0,
        "source_warehouse_counts": [
            {"warehouse_id": 2, "warehouse_name": "Building 2", "count": 1},
            {"warehouse_id": 3, "warehouse_name": "Building 3", "count": 1},
        ],
    }
    completed = _complete(
        client,
        request,
        rows,
        idempotency_key="optional-pallet-mixed-completion",
        signature=preview.json()["impact_signature"],
        accept=True,
    )

    assert completed.status_code == 200, completed.text
    session.expire_all()
    assert session.get(Box, assigned.id).pallet_id == pallet.id
    assert session.get(Box, unassigned.id).pallet_id is None
    new_box = session.scalar(
        select(Box).join(Lot).where(
            Lot.normalized_name == "new unassigned",
            Box.box_number == "001",
        )
    )
    assert new_box is not None and new_box.pallet_id is None
    assert session.scalar(
        select(func.count(PalletEvent.id)).where(
            PalletEvent.pallet_id == pallet.id,
            PalletEvent.event_type.in_(
                (
                    PalletEventType.boxes_unassigned,
                    PalletEventType.boxes_assigned,
                )
            ),
        )
    ) == 0
    snapshots = session.scalars(
        select(BoxRequestItem)
        .where(BoxRequestItem.request_id == request.id)
        .order_by(BoxRequestItem.position)
    ).all()
    by_box_id = {item.box_id: item for item in snapshots}
    assert by_box_id[assigned.id].pallet_id == pallet.id
    assert (by_box_id[unassigned.id].pallet_id, by_box_id[unassigned.id].pallet) == (
        None,
        None,
    )
    assert (by_box_id[new_box.id].pallet_id, by_box_id[new_box.id].pallet) == (
        None,
        None,
    )
    before_replay = _counts(session)
    replay = _complete(
        client,
        request,
        rows,
        idempotency_key="optional-pallet-mixed-completion",
    )
    assert replay.status_code == 200, replay.text
    assert _counts(session) == before_replay


def test_omitted_mapping_signature_tracks_preserved_assignment(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Optional Signature")
    box = _box(session, lot, "1", warehouse_id=2)
    request = _request(session, requester, quantity=1)
    session.commit()
    rows = [{"lot": lot.name, "box_number": box.box_number}]
    _as_user(requester)

    first = _preview(client, request.id, rows)
    pallet = _pallet(session, lot, "Added Later")
    box.pallet_id = pallet.id
    session.commit()
    second = _preview(client, request.id, rows)

    assert first.status_code == second.status_code == 200
    assert first.json()["rows"][0]["target_pallet_resolution"]["resolution"] == (
        "unassigned"
    )
    assert second.json()["rows"][0]["target_pallet_resolution"] == {
        "resolution": "preserve_existing",
        "pallet_id": pallet.id,
        "pallet_number": pallet.pallet_number,
    }
    assert first.json()["impact_signature"] != second.json()["impact_signature"]


def test_service_signature_tracks_inventory_and_request_version(
    session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Signature")
    box = _box(session, lot, "1", warehouse_id=2)
    request = _request(session, requester)
    session.commit()
    items = [
        InboundBoxItem(
            lot=lot.name,
            box_number=box.box_number,
            pallet_number="Target",
        )
    ]

    first = preview_inbound_completion(
        session,
        request_id=request.id,
        user=requester,
        inbound_items=items,
    )
    box.status = BoxStatus.processing
    session.commit()
    second = preview_inbound_completion(
        session,
        request_id=request.id,
        user=requester,
        inbound_items=items,
    )
    request.version += 1
    session.commit()
    third = preview_inbound_completion(
        session,
        request_id=request.id,
        user=requester,
        inbound_items=items,
    )

    assert first.impact_signature != second.impact_signature
    assert second.impact_signature != third.impact_signature
    assert first.rows[0].classification == "relocate"
    assert second.rows[0].blocked_code == "invalid_status"


def test_completion_mixes_create_and_multi_source_relocation_with_audit_and_sse(
    client, session, make_user, monkeypatch
) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = _lot(session, "Move Lot")
    source_pallet_two = _pallet(session, lot, "Source Two", warehouse_id=2)
    source_pallet_three = _pallet(session, lot, "Source Three", warehouse_id=3)
    target_pallet = _pallet(session, lot, "Target", warehouse_id=1)
    first = _box(
        session,
        lot,
        "1",
        warehouse_id=2,
        pallet=source_pallet_two,
    )
    second = _box(
        session,
        lot,
        "2",
        warehouse_id=3,
        pallet=source_pallet_three,
    )
    first.contents = "Original"
    second.contents = "Keep"
    first_received_at = datetime(2025, 1, 2, tzinfo=UTC)
    second_received_at = datetime(2025, 1, 3, tzinfo=UTC)
    first.received_at = first_received_at
    second.received_at = second_received_at
    old_requests: list[BoxRequest] = []
    for box in (first, second):
        old_request = BoxRequest(
            direction=BoxRequestDirection.inbound,
            warehouse_id=box.current_warehouse_id,
            quantity=1,
            status=BoxRequestStatus.completed,
            requester_user_id=requester.id,
            completed_at=datetime.now(UTC),
        )
        session.add(old_request)
        session.flush()
        session.add(
            BoxRequestItem(
                request_id=old_request.id,
                position=1,
                box_id=box.id,
                lot_id=lot.id,
                lot=lot.name,
                pallet_id=box.pallet_id,
                pallet=box.pallet.pallet_number,
                box_number=box.box_number,
                contents=box.contents,
            )
        )
        old_requests.append(old_request)
    request = _request(session, requester, quantity=3)
    session.commit()
    rows = [
        {
            "lot": lot.name,
            "box_number": first.box_number,
            "pallet_number": target_pallet.pallet_number,
            "pallet_id": target_pallet.id,
            "contents": "Replacement",
        },
        {
            "lot": lot.name,
            "box_number": second.box_number,
            "pallet_number": target_pallet.pallet_number,
            "pallet_id": target_pallet.id,
        },
        {
            "lot": "Fresh Lot",
            "box_number": "1",
            "pallet_number": "Fresh Target",
            "contents": "New",
        },
    ]
    _as_user(requester)
    preview = _preview(client, request.id, rows)
    assert preview.status_code == 200
    assert preview.json()["summary"]["relocated"] == 2
    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr("app.routers.requests.bus.publish", capture)
    completed = _complete(
        client,
        request,
        rows,
        idempotency_key="mixed-relocation-completion",
        signature=preview.json()["impact_signature"],
        accept=True,
    )

    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    session.expire_all()
    first = session.get(Box, first.id)
    second = session.get(Box, second.id)
    assert first is not None and second is not None
    assert (first.current_warehouse_id, second.current_warehouse_id) == (1, 1)
    assert (first.status, second.status) == (BoxStatus.received, BoxStatus.received)
    assert first.received_at == first_received_at.replace(tzinfo=None)
    assert second.received_at == second_received_at.replace(tzinfo=None)
    assert first.contents == "Replacement"
    assert second.contents == "Keep"
    assert first.pallet_id == second.pallet_id == target_pallet.id
    assert session.get(Pallet, source_pallet_two.id).is_active is False
    assert session.get(Pallet, source_pallet_three.id).is_active is False

    request_items = session.scalars(
        select(BoxRequestItem)
        .where(BoxRequestItem.request_id == request.id)
        .order_by(BoxRequestItem.position)
    ).all()
    assert len(request_items) == 3
    relocated_items = [
        item for item in request_items if item.box_id in {first.id, second.id}
    ]
    created_item = next(
        item for item in request_items if item.box_id not in {first.id, second.id}
    )
    assert {item.box_id for item in relocated_items} == {first.id, second.id}
    assert all(item.pallet_id == target_pallet.id for item in relocated_items)
    for old_request, box in zip(old_requests, (first, second), strict=True):
        old_item = session.scalar(
            select(BoxRequestItem).where(BoxRequestItem.request_id == old_request.id)
        )
        assert old_item is not None and old_item.box_id == box.id

    moved_events = session.scalars(
        select(BoxEvent)
        .where(
            BoxEvent.box_id.in_([first.id, second.id]),
            BoxEvent.event_type == BoxEventType.moved,
        )
        .order_by(BoxEvent.box_id)
    ).all()
    assert len(moved_events) == 2
    for event in moved_events:
        assert event.from_status == event.to_status == BoxStatus.received
        assert event.event_metadata["request_id"] == request.id
        assert event.event_metadata["target_warehouse_id"] == 1
        assert event.event_metadata["target_pallet_id"] == target_pallet.id
        assert event.event_metadata["inbound_existing_box_relocation"] is True
    assert moved_events[0].event_metadata["previous_contents"] == "Original"
    assert moved_events[0].event_metadata["new_contents"] == "Replacement"

    archived_pallet_events = session.scalars(
        select(PalletEvent).where(
            PalletEvent.pallet_id.in_(
                [source_pallet_two.id, source_pallet_three.id]
            ),
            PalletEvent.event_type == PalletEventType.archived,
        )
    ).all()
    assert len(archived_pallet_events) == 2
    assert all(
        event.event_metadata["inbound_existing_box_relocation"] is True
        for event in archived_pallet_events
    )
    completion_event = session.scalar(
        select(BoxRequestEvent).where(
            BoxRequestEvent.request_id == request.id,
            BoxRequestEvent.event_type == BoxRequestEventType.completed,
        )
    )
    assert completion_event is not None
    metadata = completion_event.event_metadata
    assert metadata["created_box_ids"] == [created_item.box_id]
    assert metadata["relocated_box_ids"] == [first.id, second.id]
    assert metadata["relocation_source_warehouse_counts"] == {"2": 1, "3": 1}
    assert metadata["source_warehouse_id"] is None
    assert metadata["source_warehouse_ids"] == [2, 3]
    assert metadata["target_warehouse_id"] == 1
    assert metadata["affected_pallet_warehouse_ids"] == {
        str(target_pallet.id): [1],
        str(source_pallet_two.id): [2],
        str(source_pallet_three.id): [3],
        str(created_item.pallet_id): [1],
    }
    assert metadata["accept_existing_received_boxes"] is True

    box_sse = [
        payload for event_type, payload in published if event_type == "box.updated"
    ]
    assert {payload["warehouse_id"] for payload in box_sse} == {1, 2, 3}
    assert all(payload["target_warehouse_id"] == 1 for payload in box_sse)
    assert all(payload["source_warehouse_ids"] == [2, 3] for payload in box_sse)
    pallet_sse = {
        (payload["id"], payload["warehouse_id"])
        for event_type, payload in published
        if event_type == "pallet.updated"
    }
    assert pallet_sse == {
        (target_pallet.id, 1),
        (source_pallet_two.id, 2),
        (source_pallet_three.id, 3),
        (created_item.pallet_id, 1),
    }
    before_replay = _counts(session)
    published_before_replay = len(published)
    replay = _complete(
        client,
        request,
        rows,
        idempotency_key="mixed-relocation-completion",
    )
    assert replay.status_code == 200
    assert _counts(session) == before_replay
    replay_box_sse = [
        payload
        for event_type, payload in published[published_before_replay:]
        if event_type == "box.updated"
    ]
    assert {payload["warehouse_id"] for payload in replay_box_sse} == {1, 2, 3}
    assert all(payload["source_warehouse_ids"] == [2, 3] for payload in replay_box_sse)
    replay_pallet_sse = {
        (payload["id"], payload["warehouse_id"])
        for event_type, payload in published[published_before_replay:]
        if event_type == "pallet.updated"
    }
    assert replay_pallet_sse == pallet_sse

    first.status = BoxStatus.ready_to_return
    second.status = BoxStatus.ready_to_return
    session.commit()
    candidates = client.get(f"/api/requests/{request.id}/return-candidates")
    assert candidates.status_code == 200
    assert {row["box_id"] for row in candidates.json()} >= {first.id, second.id}


def test_inbound_relocation_keeps_same_mapped_organizational_pallet(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = _lot(session, "Same Pallet Relocation")
    pallet = _pallet(session, lot, "SHARED")
    box = _box(session, lot, "1", warehouse_id=2, pallet=pallet)
    request = _request(session, requester, quantity=1)
    session.commit()
    rows = [
        {
            "lot": lot.name,
            "box_number": box.box_number,
            "pallet_number": pallet.pallet_number,
            "pallet_id": pallet.id,
        }
    ]
    _as_user(requester)
    preview = _preview(client, request.id, rows)
    assert preview.status_code == 200
    assert preview.json()["rows"][0]["classification"] == "relocate"

    completed = _complete(
        client,
        request,
        rows,
        idempotency_key="same-organizational-pallet",
        signature=preview.json()["impact_signature"],
        accept=True,
    )
    assert completed.status_code == 200, completed.text
    session.expire_all()
    moved = session.get(Box, box.id)
    assert moved.current_warehouse_id == 1
    assert moved.pallet_id == pallet.id
    assert session.get(Pallet, pallet.id).is_active is True
    assert session.scalar(
        select(func.count(PalletEvent.id)).where(
            PalletEvent.pallet_id == pallet.id,
            PalletEvent.event_type.in_(
                (
                    PalletEventType.boxes_unassigned,
                    PalletEventType.boxes_assigned,
                    PalletEventType.archived,
                )
            ),
        )
    ) == 0


def test_completion_requires_flag_and_exact_signature_for_relocations(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Gated")
    _box(session, lot, "1", warehouse_id=2)
    request = _request(session, requester, quantity=1)
    session.commit()
    rows = [{"lot": lot.name, "box_number": "1", "pallet_number": "Target"}]
    _as_user(requester)
    signature = _preview(client, request.id, rows).json()["impact_signature"]

    missing_flag = _complete(
        client,
        request,
        rows,
        idempotency_key="gated-missing-flag",
        signature=signature,
    )
    assert missing_flag.status_code == 409
    assert "explicit acceptance" in missing_flag.text
    missing_signature = _complete(
        client,
        request,
        rows,
        idempotency_key="gated-missing-signature",
        accept=True,
    )
    assert missing_signature.status_code == 409
    assert "inbound_impact_signature is required" in missing_signature.text
    stale = _complete(
        client,
        request,
        rows,
        idempotency_key="gated-stale-signature",
        signature="0" * 64,
        accept=True,
    )
    assert stale.status_code == 409
    assert "impact changed" in stale.text
    accepted = _complete(
        client,
        request,
        rows,
        idempotency_key="gated-accepted",
        signature=signature,
        accept=True,
    )
    assert accepted.status_code == 200


def test_all_new_completion_is_backward_compatible_but_validates_supplied_signature(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    request = _request(session, requester, quantity=1)
    session.commit()
    rows = [{"lot": "All New", "box_number": "1", "pallet_number": "Target"}]
    _as_user(requester)

    stale = _complete(
        client,
        request,
        rows,
        idempotency_key="all-new-stale",
        signature="0" * 64,
    )
    assert stale.status_code == 409
    completed = _complete(
        client,
        request,
        rows,
        idempotency_key="all-new-compatible",
    )
    assert completed.status_code == 200

    signed_request = _request(session, requester, quantity=1)
    session.commit()
    signed_rows = [
        {"lot": "Signed New", "box_number": "1", "pallet_number": "Signed Target"}
    ]
    signature = _preview(client, signed_request.id, signed_rows).json()[
        "impact_signature"
    ]
    signed = _complete(
        client,
        signed_request,
        signed_rows,
        idempotency_key="all-new-signed",
        signature=signature,
    )
    assert signed.status_code == 200, signed.text


def test_completion_rejects_blocked_rows_even_with_acceptance(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Blocked Completion")
    box = _box(session, lot, "1", warehouse_id=1)
    request = _request(session, requester, quantity=1)
    session.commit()
    rows = [{"lot": lot.name, "box_number": "1", "pallet_number": "Target"}]
    _as_user(requester)
    signature = _preview(client, request.id, rows).json()["impact_signature"]

    response = _complete(
        client,
        request,
        rows,
        idempotency_key="blocked-completion",
        signature=signature,
        accept=True,
    )

    assert response.status_code == 409
    assert "existing_at_target" in response.text
    assert session.get(Box, box.id).current_warehouse_id == 1
    assert session.get(BoxRequest, request.id).status == BoxRequestStatus.awaiting_confirmation


def test_relocation_rolls_back_after_partial_work(
    client, session, make_user, monkeypatch
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, "Rollback")
    source_pallet = _pallet(session, lot, "Source", warehouse_id=2)
    boxes = [
        _box(session, lot, str(index), warehouse_id=2, pallet=source_pallet)
        for index in (1, 2)
    ]
    request = _request(session, requester, quantity=2)
    session.commit()
    rows = [{"lot": lot.name, "box_number": box.box_number} for box in boxes]
    _as_user(requester)
    signature = _preview(client, request.id, rows).json()["impact_signature"]
    from app.services.boxes import _move_received_box_for_inbound_request as real_move

    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BoxRuleError("simulated relocation failure")
        return real_move(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.requests._move_received_box_for_inbound_request",
        fail_second,
    )
    response = _complete(
        client,
        request,
        rows,
        idempotency_key="rollback-relocation",
        signature=signature,
        accept=True,
    )

    assert response.status_code == 409
    session.expire_all()
    assert all(session.get(Box, box.id).current_warehouse_id == 2 for box in boxes)
    assert all(session.get(Box, box.id).pallet_id == source_pallet.id for box in boxes)
    assert session.get(Pallet, source_pallet.id).is_active is True
    assert session.get(BoxRequest, request.id).status == BoxRequestStatus.awaiting_confirmation
    assert session.scalar(
        select(func.count(BoxRequestItem.id)).where(
            BoxRequestItem.request_id == request.id
        )
    ) == 0


def test_public_box_update_does_not_gain_source_acl_bypass(
    client, session, make_user
) -> None:
    requester = make_user(UserRole.operator)
    requester.warehouses = [session.get(Warehouse, 1)]
    lot = _lot(session, "Public ACL")
    box = _box(session, lot, "1", warehouse_id=2)
    session.commit()
    _as_user(requester)

    response = client.patch(
        f"/api/boxes/{box.id}",
        json={"warehouse_id": 1},
    )

    assert response.status_code == 404
    assert session.get(Box, box.id).current_warehouse_id == 2


@pytest.mark.parametrize(
    "changed_state",
    [
        "identity_archive",
        "status",
        "warehouse",
        "current_pallet",
        "reservation",
        "target_pallet",
    ],
)
def test_completion_rejects_stale_signature_for_every_impact_dimension(
    client, session, make_user, changed_state
) -> None:
    requester = make_user(UserRole.viewer)
    lot = _lot(session, f"Stale {changed_state}")
    box = _box(session, lot, "1", warehouse_id=2)
    request = _request(session, requester, quantity=1)
    session.commit()
    rows = [
        {
            "lot": lot.name,
            "box_number": box.box_number,
            "pallet_number": f"Target {changed_state}",
        }
    ]
    _as_user(requester)
    signature = _preview(client, request.id, rows).json()["impact_signature"]

    if changed_state == "identity_archive":
        box.archived_at = datetime.now(UTC)
    elif changed_state == "status":
        box.status = BoxStatus.processing
    elif changed_state == "warehouse":
        box.current_warehouse_id = 3
    elif changed_state == "current_pallet":
        source_pallet = _pallet(
            session,
            lot,
            "New Source",
            warehouse_id=2,
        )
        box.pallet_id = source_pallet.id
    elif changed_state == "reservation":
        reservation = BoxRequest(
            direction=BoxRequestDirection.return_,
            warehouse_id=2,
            target_warehouse_id=1,
            quantity=1,
            status=BoxRequestStatus.submitted,
            requester_user_id=requester.id,
        )
        session.add(reservation)
        session.flush()
        session.add(
            BoxRequestItem(
                request_id=reservation.id,
                position=1,
                box_id=box.id,
                lot_id=lot.id,
                lot=lot.name,
                box_number=box.box_number,
            )
        )
    elif changed_state == "target_pallet":
        _pallet(session, lot, f"Target {changed_state}", warehouse_id=1)
    session.commit()

    response = _complete(
        client,
        request,
        rows,
        idempotency_key=f"stale-{changed_state}",
        signature=signature,
        accept=True,
    )

    assert response.status_code == 409
    assert "impact changed" in response.text
    assert session.get(BoxRequest, request.id).status == BoxRequestStatus.awaiting_confirmation
