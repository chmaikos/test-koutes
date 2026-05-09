"""Tests for evaluate_alerts (detect-only).

Covers the four leading-indicator scenarios the dispatcher cares about:
``low_inventory`` / ``max_capacity`` (existing) plus the new
``near_capacity``, ``near_low_inventory``, and ``box_stuck`` types.

We seed the DB directly to avoid going through the API, then invoke the
evaluator and inspect the rows it created.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models.alerts import Alert, AlertType
from app.models.boxes import Box, BoxStatus
from app.models.warehouses import Warehouse
from app.services.alerts import evaluate_alerts


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
        lot="L",
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
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "0")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=10)
    for i in range(8):  # 80% of 10 -> threshold = 8
        _box(session, box_number=f"NC-{i}", warehouse_id=1)

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
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "0")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=10)
    for i in range(10):
        _box(session, box_number=f"OC-{i}", warehouse_id=1)

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
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "0")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=5, mx=100)
    # 6 boxes -> within buffer of min(5), but still above min.
    for i in range(6):
        _box(session, box_number=f"NL-{i}", warehouse_id=1)

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
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "0")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=5, mx=100)
    # 4 boxes -> below min -> hard low_inventory only.
    for i in range(4):
        _box(session, box_number=f"NL2-{i}", warehouse_id=1)

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
# box_stuck
# ---------------------------------------------------------------------------


def test_box_stuck_counts_received_boxes_past_window(session, monkeypatch):
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "30")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=100)
    long_ago = datetime.now(UTC) - timedelta(days=45)
    recent = datetime.now(UTC) - timedelta(days=5)

    _box(session, box_number="ST-1", warehouse_id=1, received_at=long_ago)
    _box(session, box_number="ST-2", warehouse_id=1, received_at=long_ago)
    _box(session, box_number="ST-3", warehouse_id=1, received_at=recent)
    # A processed-out box of the same age should NOT be counted as stuck.
    _box(
        session,
        box_number="ST-4",
        warehouse_id=1,
        status=BoxStatus.returned,
        received_at=long_ago,
    )

    evaluate_alerts(session)

    stuck = _alerts_of_type(
        session, warehouse_id=1, alert_type=AlertType.box_stuck
    )
    assert len(stuck) == 1
    assert stuck[0].value == 2  # the two long_ago + received boxes
    assert stuck[0].threshold == 30


def test_box_stuck_resolves_when_no_more_stuck_boxes(session, monkeypatch):
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "30")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=100)
    long_ago = datetime.now(UTC) - timedelta(days=45)
    box = _box(
        session,
        box_number="ST-A",
        warehouse_id=1,
        received_at=long_ago,
    )
    evaluate_alerts(session)
    assert _alerts_of_type(
        session, warehouse_id=1, alert_type=AlertType.box_stuck
    )

    box.status = BoxStatus.ready_to_return
    session.commit()

    evaluate_alerts(session)
    assert (
        _alerts_of_type(session, warehouse_id=1, alert_type=AlertType.box_stuck)
        == []
    )


def test_box_stuck_disabled_when_threshold_is_zero(session, monkeypatch):
    monkeypatch.setenv("BOX_STUCK_THRESHOLD_DAYS", "0")
    get_settings.cache_clear()

    _set_warehouse(session, 1, mn=0, mx=100)
    long_ago = datetime.now(UTC) - timedelta(days=365)
    _box(session, box_number="ST-Z", warehouse_id=1, received_at=long_ago)

    evaluate_alerts(session)

    assert (
        _alerts_of_type(session, warehouse_id=1, alert_type=AlertType.box_stuck)
        == []
    )
