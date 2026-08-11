from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.deps import get_current_user
from app.main import app
from app.models.boxes import Box, BoxEvent, BoxEventType, BoxStatus
from app.models.lots import Lot
from app.models.requests import (
    BoxRequest,
    BoxRequestDirection,
    BoxRequestOrigin,
    BoxRequestStatus,
)
from app.models.users import UserRole
from app.models.warehouses import Warehouse
from app.services.requests import calculate_suggestion


def _lot(session: Session, name: str) -> Lot:
    lot = session.scalar(
        select(Lot).where(Lot.normalized_name == name.strip().lower())
    )
    if lot is None:
        lot = Lot(name=name)
        session.add(lot)
        session.flush()
    return lot


def _as_user(target: FastAPI, user) -> None:
    target.dependency_overrides[get_current_user] = lambda: user


def _consumption(
    session: Session,
    *,
    warehouse_id: int,
    occurred_at: datetime,
    number: int,
) -> None:
    box = Box(
        box_number=f"H-{number:04d}",
        lot_record=_lot(session, "HISTORY"),
        current_warehouse_id=warehouse_id,
        status=BoxStatus.returned,
    )
    session.add(box)
    session.flush()
    session.add(
        BoxEvent(
            box_id=box.id,
            warehouse_id=warehouse_id,
            event_type=BoxEventType.status_changed,
            from_status=BoxStatus.processing,
            to_status=BoxStatus.ready_to_return,
            occurred_at=occurred_at,
        )
    )


def _available(session: Session, *, warehouse_id: int, number: int) -> None:
    session.add(
        Box(
            box_number=f"A-{number:04d}",
            lot_record=_lot(session, "AVAILABLE"),
            current_warehouse_id=warehouse_id,
            status=BoxStatus.received,
        )
    )


def test_empty_and_sparse_history_use_minimum_gap_formula(session, make_user):
    user = make_user(UserRole.operator)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.min_inventory = 10
    warehouse.lead_time_days = 30
    warehouse.safety_stock_percent = 50
    warehouse.forecast_adjustment = 7
    _available(session, warehouse_id=1, number=1)
    _available(session, warehouse_id=1, number=2)
    session.commit()

    empty = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
    )
    assert empty.suggested_quantity == 8
    assert empty.fallback_used is True
    assert empty.lead_time_demand == 0
    assert empty.safety_stock_quantity == 0
    assert empty.adjustment_quantity == 0
    assert empty.history_days == 0
    assert empty.fallback_reason is not None
    assert empty.formula == (
        "max(0, min_inventory - current_available - pending_inbound)"
    )

    now = datetime.now(UTC)
    _consumption(session, warehouse_id=1, occurred_at=now - timedelta(days=2), number=1)
    _consumption(session, warehouse_id=1, occurred_at=now - timedelta(days=10), number=2)
    session.commit()
    sparse = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=now,
    )
    assert sparse.sample_size == 2
    assert sparse.suggested_quantity == 8
    assert sparse.confidence == "insufficient"


def test_weighted_forecast_lead_time_safety_and_adjustment(session, make_user):
    user = make_user(UserRole.operator)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.min_inventory = 0
    warehouse.max_capacity = 100
    warehouse.lead_time_days = 10
    warehouse.safety_stock_percent = 50
    warehouse.history_30_weight = 75
    warehouse.history_90_weight = 25
    warehouse.forecast_adjustment = 1
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    for number in range(9):
        _consumption(
            session,
            warehouse_id=1,
            occurred_at=now - timedelta(days=number + 1),
            number=number,
        )
    for number in range(9, 18):
        _consumption(
            session,
            warehouse_id=1,
            occurred_at=now - timedelta(days=40 + number),
            number=number,
        )
    session.commit()

    result = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=now,
    )
    assert result.history_30_quantity == 9
    assert result.history_90_quantity == 18
    assert result.daily_demand_forecast == 0.275
    assert result.lead_time_demand == 3
    assert result.safety_stock_quantity == 2
    assert result.adjustment_quantity == 1
    assert result.target_inventory == 6
    assert result.suggested_quantity == 6


def test_pending_backorders_and_scheduled_work_are_explained(session, make_user):
    user = make_user(UserRole.operator)
    now = datetime.now(UTC)
    session.add_all(
        [
            BoxRequest(
                direction=BoxRequestDirection.inbound,
                warehouse_id=1,
                quantity=4,
                status=BoxRequestStatus.submitted,
                requester_user_id=user.id,
                actual_received_quantity=1,
                scheduled_window_start=now + timedelta(days=2),
            ),
            BoxRequest(
                direction=BoxRequestDirection.inbound,
                warehouse_id=1,
                quantity=2,
                status=BoxRequestStatus.submitted,
                requester_user_id=user.id,
                origin=BoxRequestOrigin.backorder,
            ),
            BoxRequest(
                direction=BoxRequestDirection.return_,
                warehouse_id=1,
                quantity=3,
                status=BoxRequestStatus.approved,
                requester_user_id=user.id,
                scheduled_window_start=now + timedelta(days=1),
            ),
        ]
    )
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.lead_time_days = 7
    session.commit()

    result = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=now,
    )
    assert result.pending_inbound == 5
    assert result.pending_backorder == 2
    assert result.scheduled_inbound == 3
    assert result.scheduled_return == 3


def test_capacity_cap_and_return_suggestion(session, make_user):
    user = make_user(UserRole.operator)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.min_inventory = 4
    warehouse.max_capacity = 5
    warehouse.lead_time_days = 30
    now = datetime.now(UTC)
    for number in range(6):
        _consumption(
            session,
            warehouse_id=1,
            occurred_at=now - timedelta(days=number + 1),
            number=number,
        )
    _consumption(
        session,
        warehouse_id=1,
        occurred_at=now - timedelta(days=40),
        number=6,
    )
    for number in range(2):
        session.add(
            Box(
                box_number=f"R-{number}",
                lot_record=_lot(session, "RETURN"),
                current_warehouse_id=1,
                status=BoxStatus.ready_to_return,
            )
        )
    session.commit()

    inbound = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=now,
    )
    assert inbound.suggested_quantity == 3
    assert inbound.capacity_available == 3
    assert inbound.capacity_cap_applied is True

    return_result = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.return_,
        as_of=now,
    )
    assert return_result.suggested_quantity == 2
    assert return_result.formula == "eligible_return"
    assert return_result.confidence == "not_applicable"


def test_history_windows_use_application_timezone(
    session, make_user, monkeypatch
):
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Athens")
    get_settings.cache_clear()
    user = make_user(UserRole.operator)
    as_of = datetime(2026, 8, 10, 12, tzinfo=UTC)
    zone = ZoneInfo("Europe/Athens")
    local_today = as_of.astimezone(zone).date()
    boundary = datetime.combine(
        local_today - timedelta(days=29),
        datetime.min.time(),
        tzinfo=zone,
    ).astimezone(UTC)
    _consumption(
        session,
        warehouse_id=1,
        occurred_at=boundary,
        number=1,
    )
    _consumption(
        session,
        warehouse_id=1,
        occurred_at=boundary - timedelta(microseconds=1),
        number=2,
    )
    session.commit()

    result = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=as_of,
    )
    assert result.history_30_quantity == 1
    assert result.history_90_quantity == 2
    assert result.history_window_30_start == boundary
    assert result.analysis_as_of == as_of
    get_settings.cache_clear()


def test_future_and_non_consumption_events_are_excluded(
    session, make_user
):
    user = make_user(UserRole.operator)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.lead_time_days = 10
    as_of = datetime(2026, 8, 10, 12, tzinfo=UTC)
    _consumption(
        session,
        warehouse_id=1,
        occurred_at=as_of - timedelta(days=40),
        number=1,
    )
    _consumption(
        session,
        warehouse_id=1,
        occurred_at=as_of - timedelta(days=2),
        number=2,
    )
    _consumption(
        session,
        warehouse_id=1,
        occurred_at=as_of + timedelta(hours=1),
        number=3,
    )
    imported = Box(
        box_number="IMPORT",
        lot_record=_lot(session, "HISTORY"),
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    session.add(imported)
    session.flush()
    session.add(
        BoxEvent(
            box_id=imported.id,
            warehouse_id=1,
            event_type=BoxEventType.created,
            from_status=None,
            to_status=BoxStatus.received,
            occurred_at=as_of - timedelta(days=1),
        )
    )
    for _ in range(2):
        session.add(
            BoxEvent(
                box_id=imported.id,
                warehouse_id=1,
                event_type=BoxEventType.status_changed,
                from_status=BoxStatus.processing,
                to_status=BoxStatus.incomplete,
                occurred_at=as_of - timedelta(days=1),
            )
        )
    session.commit()

    first = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=as_of,
    )
    second = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=as_of,
    )
    assert first == second
    assert first.sample_size == 3
    assert first.history_days == 41
    assert "Created/imported" in first.consumption_definition


def test_upgrade_defaults_preserve_uncapped_legacy_formula(session, make_user):
    user = make_user(UserRole.operator)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.min_inventory = 5
    warehouse.max_capacity = 6
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)
    for number, days_ago in enumerate((40, 20, 10)):
        _consumption(
            session,
            warehouse_id=1,
            occurred_at=now - timedelta(days=days_ago),
            number=number,
        )
    for number in range(5):
        session.add(
            Box(
                box_number=f"U-{number}",
                lot_record=_lot(session, "OCCUPIED"),
                current_warehouse_id=1,
                status=BoxStatus.ready_to_return,
            )
        )
    session.commit()

    result = calculate_suggestion(
        session,
        user=user,
        warehouse_id=1,
        direction=BoxRequestDirection.inbound,
        as_of=now,
    )
    assert result.sample_size == 3
    assert result.history_days == 41
    assert result.capacity_available == 1
    assert result.suggested_quantity == 5
    assert result.capacity_cap_applied is False
    assert result.fallback_used is True
    assert "upgrade-safe defaults" in (result.fallback_reason or "")


def test_recommendation_snapshot_is_stable_and_old_rows_remain_compatible(
    client, session, make_user
):
    requester = make_user(UserRole.operator)
    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.min_inventory = 5
    session.commit()
    _as_user(app, requester)

    created = client.post(
        "/api/requests",
        json={"direction": "inbound", "warehouse_id": 1, "quantity": 4},
    )
    assert created.status_code == 201
    snapshot = created.json()["recommendation_snapshot"]
    assert snapshot["suggested_quantity"] == 5
    assert snapshot["analysis_as_of"]
    assert snapshot["baseline_gap"] == 5
    assert snapshot["fallback_reason"]
    assert created.json()["suggestion_quantity"] == 5

    warehouse.min_inventory = 20
    session.commit()
    fetched = client.get(f"/api/requests/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["recommendation_snapshot"] == snapshot
    assert fetched.json()["min_inventory"] == 5

    old = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.completed,
        requester_user_id=requester.id,
        recommendation_snapshot=None,
    )
    session.add(old)
    session.commit()
    old_response = client.get(f"/api/requests/{old.id}")
    assert old_response.status_code == 200
    assert old_response.json()["recommendation_snapshot"] is None


def test_planning_settings_validation_and_archived_behavior(
    client, session, make_user
):
    admin = make_user(UserRole.admin)
    operator = make_user(UserRole.operator)
    _as_user(app, admin)
    updated = client.patch(
        "/api/warehouses/1",
        json={
            "lead_time_days": 14,
            "safety_stock_percent": 25,
            "history_30_weight": 2,
            "history_90_weight": 1,
            "forecast_adjustment": -2,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["lead_time_days"] == 14

    invalid = client.patch(
        "/api/warehouses/1",
        json={"history_30_weight": 0, "history_90_weight": 0},
    )
    assert invalid.status_code == 400

    _as_user(app, operator)
    forbidden = client.patch("/api/warehouses/1", json={"lead_time_days": 3})
    assert forbidden.status_code == 403

    warehouse = session.get(Warehouse, 1)
    assert warehouse is not None
    warehouse.is_active = False
    session.commit()
    _as_user(app, admin)
    archived = client.patch("/api/warehouses/1", json={"lead_time_days": 3})
    assert archived.status_code == 409
    suggestion = client.get(
        "/api/requests/suggestion",
        params={"warehouse_id": 1, "direction": "inbound"},
    )
    assert suggestion.status_code == 400
