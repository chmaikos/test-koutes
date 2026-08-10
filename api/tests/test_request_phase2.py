from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import select

from app.deps import get_current_user
from app.main import app
from app.models.notifications import InAppNotification, RequestEmailOutbox
from app.models.requests import BoxRequest, BoxRequestEvent, BoxRequestEventType
from app.models.users import UserRole
from app.services.request_notifications import dispatch_request_email_outbox


def _as_user(target: FastAPI, user) -> None:
    target.dependency_overrides[get_current_user] = lambda: user


def _create(client, user, **extra):
    _as_user(app, user)
    response = client.post(
        "/api/requests",
        json={
            "direction": "inbound",
            "warehouse_id": 1,
            "quantity": 2,
            **extra,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_coordination_validates_assignee_and_audits_categories(
    client, session, make_user
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    created = _create(
        client,
        requester,
        priority="high",
        requested_date="2026-08-11",
        destination_contact="Loading desk",
    )
    _as_user(app, mover)
    start = datetime.now(UTC) + timedelta(hours=1)
    response = client.patch(
        f"/api/requests/{created['id']}/coordination",
        json={
            "expected_version": created["version"],
            "assigned_mover_user_id": mover.id,
            "scheduled_window_start": start.isoformat(),
            "scheduled_window_end": (start + timedelta(hours=2)).isoformat(),
            "internal_location": "Building A / Floor 2",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["assigned_mover_user_id"] == mover.id
    assert payload["internal_location"] == "Building A / Floor 2"
    assert payload["version"] == created["version"] + 1

    kinds = set(
        session.scalars(
            select(BoxRequestEvent.event_type).where(
                BoxRequestEvent.request_id == created["id"]
            )
        ).all()
    )
    assert BoxRequestEventType.assignment_changed in kinds
    assert BoxRequestEventType.schedule_changed in kinds
    assert BoxRequestEventType.coordination_changed in kinds

    stale = client.patch(
        f"/api/requests/{created['id']}/coordination",
        json={
            "expected_version": created["version"],
            "priority": "urgent",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["latest_version"] == payload["version"]


def test_assignee_requires_active_mover_with_warehouse_access(
    client, session, make_user
):
    requester = make_user(UserRole.viewer)
    invalid = make_user(UserRole.operator)
    created = _create(client, requester)
    admin = session.scalars(
        select(type(requester)).where(type(requester).role == UserRole.admin)
    ).first()
    assert admin is not None
    _as_user(app, admin)
    response = client.patch(
        f"/api/requests/{created['id']}/coordination",
        json={
            "expected_version": created["version"],
            "assigned_mover_user_id": invalid.id,
        },
    )
    assert response.status_code == 400
    assert "active admin or warehouse mover" in response.json()["detail"]


def test_comments_notifications_and_mark_read(client, session, make_user):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    created = _create(client, requester)
    _as_user(app, mover)
    assigned = client.patch(
        f"/api/requests/{created['id']}/coordination",
        json={
            "expected_version": created["version"],
            "assigned_mover_user_id": mover.id,
        },
    ).json()
    comment = client.post(
        f"/api/requests/{created['id']}/comments",
        json={"expected_version": assigned["version"], "body": "Dock 3 is available."},
    )
    assert comment.status_code == 201

    _as_user(app, requester)
    inbox = client.get("/api/notifications")
    assert inbox.status_code == 200
    assert any(item["kind"] == "comment_added" for item in inbox.json()["items"])
    unread_ids = [
        item["id"] for item in inbox.json()["items"] if item["read_at"] is None
    ]
    marked = client.post(
        "/api/notifications/mark-read",
        json={"notification_ids": unread_ids},
    )
    assert marked.status_code == 200
    assert marked.json()["unread"] == 0
    assert session.scalar(
        select(InAppNotification).where(
            InAppNotification.user_id == requester.id,
            InAppNotification.read_at.is_(None),
        )
    ) is None


def test_request_queues_search_sort_and_outbox_idempotency(
    client, session, make_user, monkeypatch
):
    requester = make_user(UserRole.viewer)
    mover = make_user(UserRole.warehouse_mover)
    overdue = _create(
        client,
        requester,
        priority="urgent",
        sla_deadline=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        internal_location="Rare archive room",
    )
    _create(
        client,
        requester,
        priority="low",
        sla_deadline=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
    )
    _as_user(app, mover)
    queue = client.get(
        "/api/requests",
        params={
            "queue": "overdue",
            "search": "Rare archive",
            "sort_by": "priority",
            "sort_dir": "desc",
        },
    )
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json()["items"]] == [overdue["id"]]

    rows = session.scalars(select(RequestEmailOutbox)).all()
    assert rows
    monkeypatch.setattr(
        "app.services.request_notifications.send_email",
        lambda **_: (True, None),
    )
    first = dispatch_request_email_outbox(session)
    second = dispatch_request_email_outbox(session)
    assert first.sent == len(rows)
    assert second.sent == 0
    assert all(row.sent_at is not None and row.ok for row in rows)

    request = session.get(BoxRequest, overdue["id"])
    assert request is not None
