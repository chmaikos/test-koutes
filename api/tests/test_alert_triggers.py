"""Tests for evaluate_alerts (detect-only).

Covers the active inventory and leading-indicator scenarios, plus the
retirement of ``box_stuck`` detection.

We seed the DB directly to avoid going through the API, then invoke the
evaluator and inspect the rows it created.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.models.alerts import Alert, AlertType
from app.models.boxes import Box, BoxStatus
from app.models.lots import Lot
from app.models.warehouses import Warehouse
from app.services.alerts import evaluate_alerts
from app.services.lots import find_lot


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_warehouse(session, wid: int, *, mn: int, mx: int) -> Warehouse:
    w = session.get(Warehouse, wid)
    w.min_inventory = mn
    w.max_capacity = mx
    session.commit()
    session.refresh(w)
    return w


def _lot(session, name: str) -> Lot:
    lot = find_lot(session, name)
    if lot is None:
        lot = Lot(name=name)
        session.add(lot)
        session.flush()
    return lot


def _box(
    session,
    *,
    box_number: str,
    warehouse_id: int,
    status: BoxStatus = BoxStatus.received,
    received_at: datetime | None = None,
) -> Box:
    b = Box(
        box_number=box_number,
        lot_record=_lot(session, "L"),
        current_warehouse_id=warehouse_id,
        status=status,
        received_at=received_at,
    )
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


def _alerts_of_type(
    session, *, warehouse_id: int, alert_type: AlertType
) -> list[Alert]:
    return list(
        session.scalars(
            select(Alert).where(
                Alert.warehouse_id == warehouse_id,
                Alert.type == alert_type,
                Alert.resolved_at.is_(None),
            )
        )
    )


# ---------------------------------------------------------------------------
# near_capacity
# ---------------------------------------------------------------------------


def test_near_capacity_fires_between_threshold_and_max(session, monkeypatch):
    monkeypatch.setenv("NEAR_CAPACITY_PERCENT", "80")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=10)
    # Near-capacity now fires against the *unavailable* backlog
    # (incomplete + ready_to_return). Seed boxes directly in
    # ``ready_to_return`` so the trigger sees them as unavailable.
    for i in range(8):  # 80% of 10 -> threshold = 8
        _box(
            session,
            box_number=f"{i + 1:03d}",
            warehouse_id=1,
            status=BoxStatus.ready_to_return,
        )

    evaluate_alerts(session)

    open_alerts = _alerts_of_type(
        session, warehouse_id=1, alert_type=AlertType.near_capacity
    )
    assert len(open_alerts) == 1
    assert open_alerts[0].value == 8
    assert open_alerts[0].threshold == 8

    # No max_capacity yet -- we are still below max.
    assert (
        _alerts_of_type(session, warehouse_id=1, alert_type=AlertType.max_capacity)
        == []
    )


def test_near_capacity_resolves_when_max_capacity_takes_over(
    session, monkeypatch
):
    monkeypatch.setenv("NEAR_CAPACITY_PERCENT", "80")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=10)
    for i in range(10):
        _box(
            session,
            box_number=f"{i + 1:03d}",
            warehouse_id=1,
            status=BoxStatus.ready_to_return,
        )

    evaluate_alerts(session)

    # max_capacity wins; near_capacity must close out so the recipient
    # doesn't get two emails for the same situation.
    assert (
        _alerts_of_type(session, warehouse_id=1, alert_type=AlertType.max_capacity)
        != []
    )
    assert (
        _alerts_of_type(session, warehouse_id=1, alert_type=AlertType.near_capacity)
        == []
    )


# ---------------------------------------------------------------------------
# near_low_inventory
# ---------------------------------------------------------------------------


def test_near_low_inventory_fires_within_buffer(session, monkeypatch):
    monkeypatch.setenv("NEAR_LOW_INVENTORY_BUFFER", "2")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=5, mx=100)
    # 6 boxes -> within buffer of min(5), but still above min.
    for i in range(6):
        _box(session, box_number=f"{i + 1:03d}", warehouse_id=1)

    evaluate_alerts(session)

    open_alerts = _alerts_of_type(
        session, warehouse_id=1, alert_type=AlertType.near_low_inventory
    )
    assert len(open_alerts) == 1
    assert open_alerts[0].value == 6
    # The stored threshold mirrors the warehouse minimum.
    assert open_alerts[0].threshold == 5

    # Hard low_inventory shouldn't fire yet.
    assert (
        _alerts_of_type(
            session, warehouse_id=1, alert_type=AlertType.low_inventory
        )
        == []
    )


def test_near_low_inventory_resolves_when_dropping_below_min(
    session, monkeypatch
):
    monkeypatch.setenv("NEAR_LOW_INVENTORY_BUFFER", "2")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=5, mx=100)
    # 4 boxes -> below min -> hard low_inventory only.
    for i in range(4):
        _box(session, box_number=f"{i + 1:03d}", warehouse_id=1)

    evaluate_alerts(session)

    assert (
        _alerts_of_type(
            session, warehouse_id=1, alert_type=AlertType.low_inventory
        )
        != []
    )
    assert (
        _alerts_of_type(
            session, warehouse_id=1, alert_type=AlertType.near_low_inventory
        )
        == []
    )


# ---------------------------------------------------------------------------
# retired box_stuck
# ---------------------------------------------------------------------------


def test_aged_received_boxes_never_open_box_stuck_alert(session):
    _set_warehouse(session, 1, mn=0, mx=100)
    long_ago = datetime.now(UTC) - timedelta(days=365)
    _box(session, box_number="001", warehouse_id=1, received_at=long_ago)
    _box(session, box_number="002", warehouse_id=1, received_at=long_ago)

    evaluate_alerts(session)

    assert (
        _alerts_of_type(session, warehouse_id=1, alert_type=AlertType.box_stuck)
        == []
    )


def test_resolved_condition_recurrence_creates_new_incident(session):
    _set_warehouse(session, 1, mn=1, mx=100)
    evaluate_alerts(session)
    first = _alerts_of_type(
        session, warehouse_id=1, alert_type=AlertType.low_inventory
    )[0]

    box = _box(session, box_number="001", warehouse_id=1)
    evaluate_alerts(session)
    session.refresh(first)
    assert first.resolved_at is not None

    box.status = BoxStatus.returned
    session.commit()
    evaluate_alerts(session)

    incidents = list(
        session.scalars(
            select(Alert)
            .where(
                Alert.warehouse_id == 1,
                Alert.type == AlertType.low_inventory,
            )
            .order_by(Alert.id)
        )
    )
    assert len(incidents) == 2
    assert incidents[0].resolved_at is not None
    assert incidents[1].resolved_at is None
    assert incidents[0].id != incidents[1].id


def test_duplicate_open_insert_reuses_canonical_without_breaking_transaction(
    session, monkeypatch
):
    from app.services import alerts as alerts_service

    _set_warehouse(session, 1, mn=1, mx=100)
    canonical = Alert(
        warehouse_id=1,
        type=AlertType.low_inventory,
        threshold=1,
        value=99,
        triggered_at=datetime.now(UTC),
    )
    session.add(canonical)
    session.commit()

    real_open_alert = alerts_service._open_alert
    first_lookup = {"missed": False}

    def _simulate_racing_lookup(db, warehouse_id, alert_type):
        if (
            warehouse_id == 1
            and alert_type == AlertType.low_inventory
            and not first_lookup["missed"]
        ):
            first_lookup["missed"] = True
            return None
        return real_open_alert(db, warehouse_id, alert_type)

    monkeypatch.setattr(alerts_service, "_open_alert", _simulate_racing_lookup)
    evaluate_alerts(session)

    rows = _alerts_of_type(
        session, warehouse_id=1, alert_type=AlertType.low_inventory
    )
    assert [row.id for row in rows] == [canonical.id]
    assert rows[0].value == 0
    assert session.scalar(select(func.count(Alert.id))) >= 1
