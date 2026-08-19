from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models.notifications import InAppNotification
from app.models.requests import (
    ACTIVE_REQUEST_STATUSES,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestEvent,
    BoxRequestEventType,
    BoxRequestException,
    BoxRequestOrigin,
    BoxRequestPriority,
    BoxRequestStatus,
)
from app.models.users import UserRole


def _as_user(user) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _version(client, request_id: int) -> int:
    return client.get(f"/api/requests/{request_id}").json()["version"]


def _action(client, request_id: int, action: str, **payload):
    if action == "complete":
        for item in payload.get("inbound_items") or []:
            item.setdefault("pallet_number", "PALLET-PHASE7")
    return client.post(
        f"/api/requests/{request_id}/{action}",
        json={"expected_version": _version(client, request_id), **payload},
    )


def _create_inbound(client, requester, *, quantity: int = 1) -> int:
    _as_user(requester)
    response = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": quantity},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _upload_delivery_note(client, request_id: int, monkeypatch) -> None:
    monkeypatch.setattr("app.routers.requests.put_document", lambda **_: None)
    response = client.post(
        f"/api/requests/{request_id}/documents",
        data={
            "document_type": "delivery_note",
            "erp_reference": f"DN-{request_id}",
            "expected_version": str(_version(client, request_id)),
        },
        files={"file": ("delivery.pdf", b"%PDF-1.7 phase7", "application/pdf")},
    )
    assert response.status_code == 201


def test_expanded_lifecycle_document_gate_audit_and_notifications(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    request_id = _create_inbound(client, requester)
    _as_user(mover)

    assert _action(client, request_id, "approve").json()["status"] == "approved"
    preparing = _action(client, request_id, "prepare")
    assert preparing.json()["status"] == "preparing"
    assert preparing.json()["preparing_by_user_id"] == mover.id
    ready = _action(client, request_id, "mark-ready")
    assert ready.json()["status"] == "ready_for_transport"
    assert ready.json()["ready_for_transport_at"] is not None

    blocked = _action(client, request_id, "start-transit")
    assert blocked.status_code == 400
    assert "current delivery_note" in blocked.json()["detail"]

    _upload_delivery_note(client, request_id, monkeypatch)
    transit = _action(client, request_id, "start-transit")
    assert transit.json()["status"] == "in_transit"
    arrived = _action(client, request_id, "mark-arrived")
    assert arrived.json()["status"] == "awaiting_confirmation"
    assert arrived.json()["awaiting_confirmation_by_user_id"] == mover.id

    _as_user(requester)
    completed = _action(
        client,
        request_id,
        "complete",
        inbound_items=[{"lot": "PHASE7", "box_number": "1"}],
        idempotency_key=f"phase7-complete-{request_id}",
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_by_user_id"] == requester.id

    event_types = set(
        session.scalars(
            select(BoxRequestEvent.event_type).where(
                BoxRequestEvent.request_id == request_id
            )
        ).all()
    )
    assert {
        BoxRequestEventType.preparation_started,
        BoxRequestEventType.ready_for_transport,
        BoxRequestEventType.in_transit,
        BoxRequestEventType.awaiting_confirmation,
        BoxRequestEventType.completed,
    } <= event_types
    notification_kinds = set(
        session.scalars(
            select(InAppNotification.kind).where(
                InAppNotification.request_id == request_id
            )
        ).all()
    )
    assert {
        "preparation_started",
        "ready_for_transport",
        "transport_started",
        "acceptance_required",
        "confirmation_received",
    } <= notification_kinds


def test_existing_in_transit_row_recovers_without_fabricated_milestones(
    client, session, make_user
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    now = datetime.now(UTC)
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.in_transit,
        requester_user_id=requester.id,
        priority=BoxRequestPriority.normal,
        origin=BoxRequestOrigin.workflow,
        suggestion_quantity=1,
        current_available=0,
        min_inventory=0,
        pending_inbound=0,
        eligible_return=0,
        submitted_at=now - timedelta(days=1),
        approved_at=now - timedelta(hours=4),
        in_transit_at=now - timedelta(hours=1),
        created_at=now - timedelta(days=1),
        updated_at=now - timedelta(hours=1),
        version=3,
    )
    session.add(request)
    session.commit()

    _as_user(mover)
    arrived = _action(client, request.id, "mark-arrived")
    assert arrived.status_code == 200
    assert arrived.json()["preparing_at"] is None
    assert arrived.json()["ready_for_transport_at"] is None

    _as_user(requester)
    completed = _action(
        client,
        request.id,
        "complete",
        inbound_items=[{"lot": "LEGACY-ACTIVE", "box_number": "1"}],
        idempotency_key=f"legacy-active-{request.id}",
    )
    assert completed.status_code == 200


def test_hold_resume_version_acl_sla_and_invalid_resume_target(
    client, session, make_user
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    operator = make_user(UserRole.operator)
    request_id = _create_inbound(client, requester)
    request = session.get(BoxRequest, request_id)
    assert request is not None
    request.sla_deadline = datetime.now(UTC) + timedelta(hours=2)
    original_deadline = request.sla_deadline
    session.commit()

    _as_user(operator)
    forbidden = _action(client, request_id, "hold", reason="Not my operation")
    assert forbidden.status_code == 403

    _as_user(mover)
    stale = client.post(
        f"/api/requests/{request_id}/hold",
        json={"expected_version": 999, "reason": "Carrier unavailable"},
    )
    assert stale.status_code == 409
    held = _action(client, request_id, "hold", reason="Carrier unavailable")
    assert held.status_code == 200
    assert held.json()["current_exception"]["exception_kind"] == "hold"

    exception = session.scalar(
        select(BoxRequestException).where(
            BoxRequestException.request_id == request_id,
            BoxRequestException.resolved_at.is_(None),
        )
    )
    assert exception is not None
    exception.resume_target = BoxRequestStatus.completed
    session.commit()
    invalid = _action(client, request_id, "resume", resolution="Carrier restored")
    assert invalid.status_code == 409

    exception.resume_target = BoxRequestStatus.submitted
    session.commit()
    resumed = _action(client, request_id, "resume", resolution="Carrier restored")
    assert resumed.status_code == 200
    assert resumed.json()["current_exception"] is None
    session.refresh(request)
    assert request.sla_deadline is not None
    persisted_deadline = (
        request.sla_deadline.replace(tzinfo=UTC)
        if request.sla_deadline.tzinfo is None
        else request.sla_deadline
    )
    assert persisted_deadline >= original_deadline


def test_failed_transport_retry_and_reschedule_recovery(
    client, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    request_id = _create_inbound(client, requester)
    _as_user(mover)
    for action in ("approve", "prepare", "mark-ready"):
        assert _action(client, request_id, action).status_code == 200
    _upload_delivery_note(client, request_id, monkeypatch)
    assert _action(client, request_id, "start-transit").status_code == 200

    start = datetime.now(UTC) + timedelta(days=1)
    failed = _action(
        client,
        request_id,
        "report-failed-delivery",
        reason="Recipient unavailable",
        revised_window_start=start.isoformat(),
        revised_window_end=(start + timedelta(hours=2)).isoformat(),
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == "in_transit"
    assert failed.json()["current_exception"]["resume_target"] == "ready_for_transport"
    assert _action(client, request_id, "mark-arrived").status_code == 409

    retried = _action(
        client,
        request_id,
        "retry-transport",
        resolution="Recipient confirmed the revised window",
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "ready_for_transport"
    assert retried.json()["current_exception"] is None
    persisted_start = datetime.fromisoformat(
        retried.json()["scheduled_window_start"].replace("Z", "+00:00")
    )
    if persisted_start.tzinfo is None:
        persisted_start = persisted_start.replace(tzinfo=UTC)
    assert persisted_start == start
    assert _action(client, request_id, "start-transit").status_code == 200
    assert _action(client, request_id, "mark-arrived").status_code == 200


@pytest.mark.parametrize("request_status", ACTIVE_REQUEST_STATUSES)
def test_all_expanded_active_states_count_as_pending_and_block_archive(
    client, session, make_user, request_status
):
    admin = make_user(UserRole.admin)
    now = datetime.now(UTC)
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=3,
        status=request_status,
        requester_user_id=admin.id,
        priority=BoxRequestPriority.normal,
        origin=BoxRequestOrigin.workflow,
        suggestion_quantity=3,
        current_available=0,
        min_inventory=0,
        pending_inbound=0,
        eligible_return=0,
        submitted_at=now,
        created_at=now,
        updated_at=now,
        version=1,
    )
    session.add(request)
    session.commit()
    _as_user(admin)

    suggestion = client.get(
        "/api/requests/suggestion",
        params={"warehouse_id": 1, "direction": "inbound"},
    )
    assert suggestion.status_code == 200
    assert suggestion.json()["pending_inbound"] == 3
    archived = client.delete("/api/warehouses/1")
    assert archived.status_code == 409
    assert archived.json()["detail"]["active_requests"] == 1
