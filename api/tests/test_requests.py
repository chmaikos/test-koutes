from __future__ import annotations

from io import BytesIO

from fastapi import FastAPI
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.main import app
from app.models.boxes import Box, BoxStatus
from app.models.requests import (
    BoxRequest,
    BoxRequestDirection,
    BoxRequestDocument,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestStatus,
)
from app.models.users import UserRole
from app.models.warehouses import Warehouse
from app.services.boxes import create_box
from app.services.object_storage import StorageUnavailableError


def _as_user(app: FastAPI, user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _version(client, request_id: int) -> int:
    return client.get(f"/api/requests/{request_id}").json()["version"]


def _action(client, request_id: int, action: str, **payload):
    if action == "start-transit":
        current_status = client.get(f"/api/requests/{request_id}").json()["status"]
        if current_status == "approved":
            prepared = _action(client, request_id, "prepare")
            assert prepared.status_code == 200
            ready = _action(client, request_id, "mark-ready")
            assert ready.status_code == 200
    if action == "complete":
        current_status = client.get(f"/api/requests/{request_id}").json()["status"]
        if current_status == "in_transit":
            arrived = _action(client, request_id, "mark-arrived")
            assert arrived.status_code == 200
    payload["expected_version"] = _version(client, request_id)
    if action == "complete":
        payload.setdefault("idempotency_key", f"test-completion-{request_id}")
    return client.post(f"/api/requests/{request_id}/{action}", json=payload)


def _document_data(client, request_id: int, **data):
    return {
        **data,
        "expected_version": str(_version(client, request_id)),
    }


def _box(session: Session, user, number: str, status: BoxStatus = BoxStatus.received) -> Box:
    box = create_box(
        session,
        user=user,
        box_number=number,
        lot="LOT-A",
        warehouse_id=1,
    )
    if status != BoxStatus.received:
        box.status = status
        session.commit()
        session.refresh(box)
    return box


def _start_inbound_delivery(
    client,
    requester,
    mover,
    monkeypatch,
    *,
    quantity: int,
) -> int:
    _as_user(app, requester)
    request_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": quantity},
    ).json()["id"]
    _as_user(app, mover)
    assert _action(client, request_id, "approve").status_code == 200
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    assert (
        client.post(
            f"/api/requests/{request_id}/documents",
            data=_document_data(
                client,
                request_id,
                document_type="delivery_note",
                erp_reference=f"DN-{request_id}",
            ),
            files={"file": ("note.pdf", b"%PDF-1.7 test", "application/pdf")},
        ).status_code
        == 201
    )
    assert (
        _action(client, request_id, "start-transit").status_code
        == 200
    )
    assert _action(client, request_id, "mark-arrived").status_code == 200
    _as_user(app, requester)
    return request_id


def _complete_inbound_order(
    client,
    session,
    requester,
    mover,
    monkeypatch,
    *,
    quantity: int,
    lot: str = "RETURN-SOURCE",
) -> tuple[int, list[Box]]:
    request_id = _start_inbound_delivery(
        client, requester, mover, monkeypatch, quantity=quantity
    )
    completed = _action(
        client,
        request_id,
        "complete",
        inbound_items=[
            {"lot": lot, "box_number": str(index)}
            for index in range(1, quantity + 1)
        ],
    )
    assert completed.status_code == 200
    boxes = list(
        session.scalars(
            select(Box).where(Box.lot == lot).order_by(Box.box_number.asc())
        ).all()
    )
    assert len(boxes) == quantity
    return request_id, boxes


def test_inbound_suggestion_accounts_for_pending_requests(client, session, make_user):
    warehouse = session.get(Warehouse, 1)
    warehouse.min_inventory = 5
    session.commit()

    requester = make_user(UserRole.viewer)
    operator = make_user(UserRole.operator)
    _box(session, operator, "1")
    _box(session, operator, "2")
    _as_user(app, requester)

    suggestion = client.get(
        "/api/requests/suggestion",
        params={"warehouse_id": 1, "direction": "inbound"},
    )
    assert suggestion.status_code == 200
    assert suggestion.json()["suggested_quantity"] == 3

    created = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 2},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "submitted"

    suggestion = client.get(
        "/api/requests/suggestion",
        params={"warehouse_id": 1, "direction": "inbound"},
    )
    assert suggestion.json()["pending_inbound"] == 2
    assert suggestion.json()["suggested_quantity"] == 1


def test_inbound_workflow_materializes_boxes_only_on_acceptance(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(app, requester)
    created = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 2},
    ).json()
    request_id = created["id"]
    assert session.scalar(select(func.count(Box.id))) == 0

    _as_user(app, mover)
    assert _action(client, request_id, "approve").status_code == 200
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    uploaded = client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="delivery_note",
            erp_reference="DN-100",
        ),
        files={"file": ("delivery-note.pdf", b"%PDF-1.7 test", "application/pdf")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["erp_reference"] == "DN-100"
    assert (
        _action(client, request_id, "start-transit").status_code
        == 200
    )
    assert session.scalar(select(func.count(Box.id))) == 0
    assert _action(client, request_id, "mark-arrived").status_code == 200

    _as_user(app, requester)
    completed = _action(
        client,
        request_id,
        "complete",
        inbound_items=[
            {"lot": "NEW-LOT", "box_number": "1", "contents": "A"},
            {
                "lot": "NEW-LOT",
                "box_number": "001",
                "contents": "A extra",
            },
            {"lot": "NEW-LOT", "box_number": "2", "contents": "B"},
        ],
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["actual_received_quantity"] == 2
    assert completed.json()["variance_quantity"] == 0
    assert completed.json()["discrepancy_reason"] is None
    boxes = session.scalars(select(Box).order_by(Box.box_number)).all()
    assert [
        (box.lot, box.box_number, box.contents, box.status) for box in boxes
    ] == [
        ("NEW-LOT", "001", "A | A extra", BoxStatus.received),
        ("NEW-LOT", "002", "B", BoxStatus.received),
    ]
    repeated = client.post(
        f"/api/requests/{request_id}/complete",
        json={
            "expected_version": created["version"],
            "idempotency_key": f"test-completion-{request_id}",
        },
    )
    assert repeated.status_code == 200
    assert session.scalar(select(func.count(Box.id))) == 2


def test_short_inbound_delivery_completes_with_audited_variance(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    request_id = _start_inbound_delivery(
        client, requester, mover, monkeypatch, quantity=3
    )

    completed = _action(
        client,
        request_id,
        "complete",
        inbound_items=[
            {"lot": "SHORT", "box_number": "1"},
            {"lot": "SHORT", "box_number": "2"},
        ],
        discrepancy_reason="Two boxes were available at dispatch.",
    )

    assert completed.status_code == 200
    assert completed.json()["quantity"] == 3
    assert completed.json()["actual_received_quantity"] == 2
    assert completed.json()["variance_quantity"] == -1
    assert (
        completed.json()["discrepancy_reason"]
        == "Two boxes were available at dispatch."
    )
    assert session.scalar(select(func.count(Box.id))) == 2
    assert len(completed.json()["discrepancies"]) == 1
    assert completed.json()["discrepancies"][0]["discrepancy_type"] == "missing"
    child_id = completed.json()["child_request_ids"][0]
    child = client.get(f"/api/requests/{child_id}").json()
    assert child["status"] == "submitted"
    assert child["origin"] == "backorder"
    assert child["quantity"] == 1
    assert child["parent_request_id"] == request_id
    assert child["root_request_id"] == request_id
    photo = client.post(
        f"/api/requests/{request_id}/discrepancies/"
        f"{completed.json()['discrepancies'][0]['id']}/photos",
        data={"expected_version": str(completed.json()["version"])},
        files={"file": ("shortage.jpg", b"\xff\xd8\xffphoto", "image/jpeg")},
    )
    assert photo.status_code == 201
    assert photo.json()["original_filename"] == "shortage.jpg"
    event = session.scalar(
        select(BoxRequestEvent).where(
            BoxRequestEvent.request_id == request_id,
            BoxRequestEvent.event_type == BoxRequestEventType.partial_completion,
        )
    )
    assert event is not None
    assert "Ordered 3; received 2; variance -1" in (event.note or "")
    assert "Two boxes were available at dispatch." in (event.note or "")


def test_over_delivery_groups_duplicates_before_recording_variance(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    request_id = _start_inbound_delivery(
        client, requester, mover, monkeypatch, quantity=2
    )

    completed = _action(
        client,
        request_id,
        "complete",
        inbound_items=[
            {"lot": "OVER", "box_number": "1", "contents": "A"},
            {"lot": "OVER", "box_number": "001", "contents": "B"},
            {"lot": "OVER", "box_number": "2"},
            {"lot": "OVER", "box_number": "3"},
        ],
        discrepancy_reason="Accepted an extra prepared box.",
    )

    assert completed.status_code == 200
    assert completed.json()["actual_received_quantity"] == 3
    assert completed.json()["variance_quantity"] == 1
    assert session.scalar(select(func.count(Box.id))) == 3
    first = session.scalar(select(Box).where(Box.box_number == "001"))
    assert first is not None
    assert first.contents == "A | B"
    repeated = client.post(
        f"/api/requests/{request_id}/complete",
        json={"expected_version": 1, "idempotency_key": f"test-completion-{request_id}"},
    )
    assert repeated.status_code == 200
    assert session.scalar(select(func.count(Box.id))) == 3


def test_inbound_variance_requires_reason_and_creates_no_inventory(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    request_id = _start_inbound_delivery(
        client, requester, mover, monkeypatch, quantity=2
    )

    response = _action(
        client,
        request_id,
        "complete",
        inbound_items=[{"lot": "SHORT", "box_number": "1"}],
    )

    assert response.status_code == 400
    assert "missing discrepancy" in response.json()["detail"]
    assert session.scalar(select(func.count(Box.id))) == 0
    request = session.get(BoxRequest, request_id)
    assert request is not None
    assert request.status == BoxRequestStatus.awaiting_confirmation
    assert request.actual_received_quantity is None


def test_return_workflow_reserves_and_returns_specific_boxes(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    source_id, boxes = _complete_inbound_order(
        client, session, requester, mover, monkeypatch, quantity=3
    )
    for box in boxes:
        box.status = BoxStatus.ready_to_return
    session.commit()

    _as_user(app, requester)
    sources = client.get(
        "/api/requests/return-sources", params={"warehouse_id": 1}
    )
    assert sources.status_code == 200
    source = next(item for item in sources.json() if item["id"] == source_id)
    assert source["delivered_quantity"] == 3
    assert source["eligible_quantity"] == 3
    candidates = client.get(f"/api/requests/{source_id}/return-candidates")
    assert candidates.status_code == 200
    assert [item["box_id"] for item in candidates.json()] == [
        box.id for box in boxes
    ]

    created = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 2,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[0].id, boxes[2].id],
        },
    )
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["source_inbound_request_id"] == source_id
    assert [item["box_id"] for item in created.json()["items"]] == [
        boxes[0].id,
        boxes[2].id,
    ]
    remaining = client.get(f"/api/requests/{source_id}/return-candidates").json()
    assert [item["box_id"] for item in remaining] == [boxes[1].id]

    _as_user(app, mover)
    _action(client, request_id, "approve")
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="return_note",
            erp_reference="RN-100",
        ),
        files={"file": ("return-note.pdf", b"%PDF-1.7 test", "application/pdf")},
    )
    _action(client, request_id, "start-transit")
    completed = _action(client, request_id, "complete")
    assert completed.status_code == 200
    assert completed.json()["actual_received_quantity"] == 2
    assert completed.json()["variance_quantity"] == 0
    assert session.get(Box, boxes[0].id).status == BoxStatus.returned
    assert session.get(Box, boxes[2].id).status == BoxStatus.returned
    assert session.get(Box, boxes[1].id).status == BoxStatus.ready_to_return

    _as_user(app, requester)
    follow_up = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[1].id],
        },
    )
    assert follow_up.status_code == 201


def test_return_source_selection_validates_and_releases_reservations(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    source_id, boxes = _complete_inbound_order(
        client,
        session,
        requester,
        mover,
        monkeypatch,
        quantity=2,
        lot="RELEASE-SOURCE",
    )
    for box in boxes:
        box.status = BoxStatus.ready_to_return
    outside = _box(session, requester, "99", BoxStatus.ready_to_return)
    session.commit()
    _as_user(app, requester)

    missing_source = client.post(
        "/api/requests",
        json={"direction": "return", "warehouse_id": 1, "quantity": 1},
    )
    assert missing_source.status_code == 400
    outside_source = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [outside.id],
        },
    )
    assert outside_source.status_code == 409

    first_return = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[0].id],
        },
    )
    assert first_return.status_code == 201
    conflict = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[0].id],
        },
    )
    assert conflict.status_code == 409

    cancelled = _action(
        client,
        first_return.json()["id"],
        "cancel",
        reason="Return later",
    )
    assert cancelled.status_code == 200
    assert {
        item["box_id"]
        for item in client.get(
            f"/api/requests/{source_id}/return-candidates"
        ).json()
    } == {box.id for box in boxes}

    retried = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[0].id],
        },
    )
    _as_user(app, mover)
    rejected = _action(
        client,
        retried.json()["id"],
        "reject",
        reason="Not collecting today",
    )
    assert rejected.status_code == 200
    _as_user(app, requester)
    assert {
        item["box_id"]
        for item in client.get(
            f"/api/requests/{source_id}/return-candidates"
        ).json()
    } == {box.id for box in boxes}


def test_partial_return_creates_non_reserving_reselection_draft(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    source_id, boxes = _complete_inbound_order(
        client,
        session,
        requester,
        mover,
        monkeypatch,
        quantity=2,
        lot="PARTIAL-RETURN",
    )
    for box in boxes:
        box.status = BoxStatus.ready_to_return
    session.commit()
    _as_user(app, requester)
    created = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 2,
            "source_inbound_request_id": source_id,
            "box_ids": [box.id for box in boxes],
        },
    ).json()
    _as_user(app, mover)
    _action(client, created["id"], "approve")
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    client.post(
        f"/api/requests/{created['id']}/documents",
        data=_document_data(
            client,
            created["id"],
            document_type="return_note",
            erp_reference="RN-PARTIAL",
        ),
        files={"file": ("return.pdf", b"%PDF- partial", "application/pdf")},
    )
    _action(client, created["id"], "start-transit")
    completed = _action(
        client,
        created["id"],
        "complete",
        collected_box_ids=[boxes[0].id],
        discrepancy_reason="Second box was not collected.",
    )
    assert completed.status_code == 200
    result = completed.json()
    assert result["actual_received_quantity"] == 1
    assert result["variance_quantity"] == -1
    assert session.get(Box, boxes[0].id).status == BoxStatus.returned
    assert session.get(Box, boxes[1].id).status == BoxStatus.ready_to_return
    draft = client.get(f"/api/requests/{result['child_request_ids'][0]}").json()
    assert draft["status"] == "draft"
    assert draft["origin"] == "return_reselection"
    assert draft["items"] == []
    assert draft["parent_request_id"] == created["id"]
    _as_user(app, requester)
    available = client.get(f"/api/requests/{source_id}/return-candidates").json()
    assert [item["box_id"] for item in available] == [boxes[1].id]
    submitted = client.post(
        f"/api/requests/{draft['id']}/submit-draft",
        json={
            "expected_version": draft["version"],
            "box_ids": [boxes[1].id],
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "submitted"
    assert submitted.json()["items"][0]["box_id"] == boxes[1].id
    assert client.get(f"/api/requests/{source_id}/return-candidates").json() == []


def test_return_candidates_require_completed_source_and_ready_boxes(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(app, requester)
    incomplete_source_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    ).json()["id"]
    not_completed = client.get(
        f"/api/requests/{incomplete_source_id}/return-candidates"
    )
    assert not_completed.status_code == 409

    source_id, boxes = _complete_inbound_order(
        client,
        session,
        requester,
        mover,
        monkeypatch,
        quantity=2,
        lot="READY-FILTER",
    )
    boxes[0].status = BoxStatus.ready_to_return
    boxes[1].status = BoxStatus.processing
    session.commit()
    _as_user(app, requester)
    candidates = client.get(f"/api/requests/{source_id}/return-candidates")
    assert candidates.status_code == 200
    assert [item["box_id"] for item in candidates.json()] == [boxes[0].id]


def test_return_completion_is_atomic_and_legacy_source_link_is_optional(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    source_id, boxes = _complete_inbound_order(
        client,
        session,
        requester,
        mover,
        monkeypatch,
        quantity=2,
        lot="LEGACY-RETURN",
    )
    for box in boxes:
        box.status = BoxStatus.ready_to_return
    session.commit()
    _as_user(app, requester)
    created = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 2,
            "source_inbound_request_id": source_id,
            "box_ids": [box.id for box in boxes],
        },
    ).json()
    legacy_request = session.get(BoxRequest, created["id"])
    legacy_request.source_inbound_request_id = None
    session.commit()

    _as_user(app, mover)
    _action(client, created["id"], "approve")
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    client.post(
        f"/api/requests/{created['id']}/documents",
        data=_document_data(
            client,
            created["id"],
            document_type="return_note",
            erp_reference="RN-LEGACY",
        ),
        files={"file": ("return-note.pdf", b"%PDF-1.7 test", "application/pdf")},
    )
    _action(client, created["id"], "start-transit")
    boxes[1].status = BoxStatus.processing
    session.commit()
    failed = _action(client, created["id"], "complete")
    assert failed.status_code == 409
    assert session.get(Box, boxes[0].id).status == BoxStatus.ready_to_return
    assert session.get(BoxRequest, created["id"]).source_inbound_request_id is None
    boxes[1].status = BoxStatus.ready_to_return
    session.commit()
    completed = _action(client, created["id"], "complete")
    assert completed.status_code == 200
    assert completed.json()["source_inbound_request_id"] is None


def test_admin_override_cancels_conflicting_return_and_audits_reason(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    admin = make_user(UserRole.admin)
    source_id, boxes = _complete_inbound_order(
        client,
        session,
        requester,
        mover,
        monkeypatch,
        quantity=1,
        lot="FORCED-MOVE",
    )
    boxes[0].status = BoxStatus.ready_to_return
    session.commit()
    _as_user(app, requester)
    return_request = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[0].id],
        },
    ).json()

    _as_user(app, admin)
    moved = client.patch(
        f"/api/boxes/{boxes[0].id}",
        json={
            "warehouse_id": 2,
            "force": True,
            "note": "Physical inventory correction",
        },
    )
    assert moved.status_code == 200
    cancelled = client.get(f"/api/requests/{return_request['id']}").json()
    assert cancelled["status"] == "cancelled"
    assert "Physical inventory correction" in cancelled["cancellation_reason"]
    events = client.get(f"/api/requests/{return_request['id']}/events").json()
    assert events[0]["event_type"] == "cancelled"


def test_return_completion_rejects_wrong_warehouse(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.operator)
    mover = make_user(UserRole.warehouse_mover)
    source_id, boxes = _complete_inbound_order(
        client,
        session,
        requester,
        mover,
        monkeypatch,
        quantity=1,
        lot="WAREHOUSE-RACE",
    )
    boxes[0].status = BoxStatus.ready_to_return
    session.commit()
    _as_user(app, requester)
    return_id = client.post(
        "/api/requests",
        json={
            "direction": "return",
            "warehouse_id": 1,
            "quantity": 1,
            "source_inbound_request_id": source_id,
            "box_ids": [boxes[0].id],
        },
    ).json()["id"]
    _as_user(app, mover)
    _action(client, return_id, "approve")
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    client.post(
        f"/api/requests/{return_id}/documents",
        data=_document_data(
            client,
            return_id,
            document_type="return_note",
            erp_reference="RN-WH",
        ),
        files={"file": ("return.pdf", b"%PDF-1.7 test", "application/pdf")},
    )
    _action(client, return_id, "start-transit")
    boxes[0].current_warehouse_id = 2
    session.commit()
    completed = _action(client, return_id, "complete")
    assert completed.status_code == 409
    assert session.get(Box, boxes[0].id).status == BoxStatus.ready_to_return


def test_only_movers_can_approve_and_requester_can_cancel(client, session, make_user):
    requester = make_user(UserRole.viewer)
    other = make_user(UserRole.operator)
    _as_user(app, requester)
    request_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    ).json()["id"]

    _as_user(app, other)
    assert client.post(f"/api/requests/{request_id}/approve", json={}).status_code == 403
    assert _action(client, request_id, "cancel").status_code == 403

    _as_user(app, requester)
    cancelled = _action(
        client, request_id, "cancel", reason="No longer needed"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == BoxRequestStatus.cancelled.value


def test_request_mutations_require_versions_and_conflicts_are_structured(
    client, make_user
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(app, requester)
    created = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    ).json()
    _as_user(app, mover)
    assert client.post(f"/api/requests/{created['id']}/approve", json={}).status_code == 422
    approved = _action(client, created["id"], "approve")
    assert approved.status_code == 200
    conflict = client.post(
        f"/api/requests/{created['id']}/start-transit",
        json={"expected_version": created["version"]},
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["request_id"] == created["id"]
    assert detail["latest_version"] == approved.json()["version"]
    assert detail["latest_status"] == "approved"
    assert detail["relevant_events"][0]["event_type"] == "approved"


def test_document_download_is_acl_checked(client, session, make_user, monkeypatch):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(app, requester)
    request_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    ).json()["id"]
    _as_user(app, mover)
    _action(client, request_id, "approve")
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    document = client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="delivery_note",
            erp_reference="DN-200",
        ),
        files={"file": ("note.pdf", b"%PDF- test", "application/pdf")},
    ).json()

    class Body(BytesIO):
        pass

    monkeypatch.setattr(
        "app.routers.requests.get_document",
        lambda _: (Body(b"%PDF- test"), 10),
    )
    response = client.post(
        f"/api/requests/{request_id}/documents/{document['id']}/download"
    )
    assert response.status_code == 200
    assert response.content == b"%PDF- test"
    assert response.headers["content-length"] == "10"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    stored = session.get(BoxRequestDocument, document["id"])
    assert stored.sha256
    request = session.get(BoxRequest, request_id)
    assert request.direction == BoxRequestDirection.inbound

    monkeypatch.setattr(
        "app.routers.requests.get_document",
        lambda _: (Body(b"damaged"), 7),
    )
    corrupted = client.get(
        f"/api/requests/{request_id}/documents/{document['id']}/download"
    )
    assert corrupted.status_code == 502
    assert "integrity check" in corrupted.json()["detail"]

    outsider = make_user(UserRole.viewer)
    outsider.warehouses = []
    session.commit()
    _as_user(app, outsider)
    denied = client.get(
        f"/api/requests/{request_id}/documents/{document['id']}/download"
    )
    assert denied.status_code == 404


def test_import_receipt_owner_can_upload_erp_document(
    client, make_user, monkeypatch
):
    owner = make_user(UserRole.operator)
    outsider = make_user(UserRole.operator)
    _as_user(app, owner)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["box_number", "lot", "warehouse_id"])
    sheet.append(["1", "SELF-RECEIPT", 1])
    payload = BytesIO()
    workbook.save(payload)
    imported = client.post(
        "/api/boxes/import",
        files={
            "file": (
                "receipt.xlsx",
                payload.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert imported.status_code == 200
    receipt_id = imported.json()["receipt_request_ids"][0]
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    uploaded = client.post(
        f"/api/requests/{receipt_id}/documents",
        data=_document_data(
            client,
            receipt_id,
            document_type="delivery_note",
            erp_reference="DN-IMPORT-1",
        ),
        files={
            "file": (
                "import-delivery.pdf",
                b"%PDF- imported receipt",
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["erp_reference"] == "DN-IMPORT-1"

    _as_user(app, outsider)
    denied = client.post(
        f"/api/requests/{receipt_id}/documents",
        data=_document_data(
            client,
            receipt_id,
            document_type="other",
            erp_reference="OTHER",
        ),
        files={"file": ("other.pdf", b"%PDF- denied", "application/pdf")},
    )
    assert denied.status_code == 403


def test_document_replacement_retains_audit_version(client, session, make_user, monkeypatch):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(app, requester)
    request_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    ).json()["id"]
    _as_user(app, mover)
    _action(client, request_id, "approve")
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)

    invalid = client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="delivery_note",
            erp_reference="DN-BAD",
        ),
        files={"file": ("note.txt", b"not allowed", "text/plain")},
    )
    assert invalid.status_code == 415

    first = client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="delivery_note",
            erp_reference="DN-1",
        ),
        files={"file": ("note.pdf", b"%PDF- first", "application/pdf")},
    ).json()
    second = client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="delivery_note",
            erp_reference="DN-2",
        ),
        files={"file": ("note.pdf", b"%PDF- second", "application/pdf")},
    ).json()
    documents = client.get(f"/api/requests/{request_id}/documents").json()
    by_id = {document["id"]: document for document in documents}
    assert by_id[first["id"]]["is_current"] is False
    assert by_id[second["id"]]["is_current"] is True
    assert len(documents) == 2


def test_document_upload_reports_unavailable_storage(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    _as_user(app, requester)
    request_id = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 1},
    ).json()["id"]
    _as_user(app, mover)
    _action(client, request_id, "approve")

    def unavailable(**_):
        raise StorageUnavailableError("object storage is unavailable")

    monkeypatch.setattr("app.routers.requests.put_document", unavailable)
    response = client.post(
        f"/api/requests/{request_id}/documents",
        data=_document_data(
            client,
            request_id,
            document_type="delivery_note",
            erp_reference="DN-500",
        ),
        files={"file": ("note.pdf", b"%PDF- unavailable", "application/pdf")},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "object storage is unavailable"
