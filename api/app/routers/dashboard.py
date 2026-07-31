from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from sqlalchemy import func, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models.alerts import Alert
from app.models.boxes import (
    ACTIVE_STATUSES,
    AVAILABLE_STATUSES,
    UNAVAILABLE_STATUSES,
    Box,
    BoxEvent,
    BoxEventType,
    BoxStatus,
)
from app.models.warehouses import Warehouse
from app.schemas.dashboard import DashboardSummary, WarehouseSummary
from app.schemas.employees import PerformerOut, WarehouseProductivityOut
from app.services.acl import apply_warehouse_filter
from app.services.productivity import (
    WarehouseProductivity,
    daily_summary,
    weekly_summary,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _app_tz() -> ZoneInfo:
    """Resolve ``APP_TIMEZONE`` to a real ZoneInfo (UTC on bad input)."""
    tz_name = get_settings().app_timezone or "UTC"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _today_in_app_tz() -> date:
    """Today's date in the configured ``APP_TIMEZONE`` (default UTC).

    Mirrors the helper in :mod:`app.routers.productivity`. We duplicate
    the tiny implementation here rather than importing it because the
    productivity router depends on this one transitively (via its
    response schemas) and a top-level import would risk a cycle.
    """
    return datetime.now(_app_tz()).date()


def _local_day_bounds_utc() -> tuple[datetime, datetime, float]:
    """Return (start, end, elapsed_hours) for "today" in APP_TIMEZONE.

    ``start`` / ``end`` are timezone-aware UTC instants so they can be
    compared directly against the timezone-aware ``occurred_at`` column.
    ``elapsed_hours`` is how far we are into the local day, floored at
    1.0 so a divide-by-zero never sneaks into the projected per-day
    completion rate at the very start of the day.
    """
    tz = _app_tz()
    local_now = datetime.now(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_tomorrow = local_midnight + timedelta(days=1)
    start_utc = local_midnight.astimezone(UTC)
    end_utc = local_tomorrow.astimezone(UTC)
    elapsed = (local_now - local_midnight).total_seconds() / 3600.0
    return start_utc, end_utc, max(1.0, elapsed)


def _to_productivity_out(
    summary: WarehouseProductivity,
) -> WarehouseProductivityOut:
    return WarehouseProductivityOut(
        warehouse_id=summary.warehouse_id,
        total_pages=summary.total_pages,
        total_hours=summary.total_hours,
        avg_pages_per_day=summary.avg_pages_per_day,
        entry_count=summary.entry_count,
        active_employees=summary.active_employees,
        top=[
            PerformerOut(
                employee_id=p.employee_id,
                employee_name=p.employee_name,
                pages=p.pages,
                hours=p.hours,
                pages_per_day=p.pages_per_day,
            )
            for p in summary.top
        ],
        bottom=[
            PerformerOut(
                employee_id=p.employee_id,
                employee_name=p.employee_name,
                pages=p.pages,
                hours=p.hours,
                pages_per_day=p.pages_per_day,
            )
            for p in summary.bottom
        ],
    )


@router.get("/summary", response_model=DashboardSummary)
def summary(db: DbSession, user: CurrentUser) -> DashboardSummary:
    # The "today" window for the daily counters (received / returned /
    # completed) is in APP_TIMEZONE so it agrees with the productivity
    # boundary; ``_local_day_bounds_utc`` hands us the equivalent UTC
    # instants for the comparison.
    midnight, tomorrow, elapsed_hours = _local_day_bounds_utc()

    warehouses = db.scalars(
        apply_warehouse_filter(
            select(Warehouse).where(Warehouse.is_active.is_(True)),
            user,
            Warehouse.id,
        ).order_by(Warehouse.id)
    ).all()

    status_rows = db.execute(
        apply_warehouse_filter(
            select(Box.current_warehouse_id, Box.status, func.count(Box.id)),
            user,
            Box.current_warehouse_id,
        )
        .where(Box.archived_at.is_(None))
        .group_by(Box.current_warehouse_id, Box.status)
    ).all()
    by_warehouse_status: dict[int, dict[BoxStatus, int]] = {}
    for wid, st, c in status_rows:
        by_warehouse_status.setdefault(wid, {})[st] = int(c)

    received_today_rows = db.execute(
        apply_warehouse_filter(
            select(BoxEvent.warehouse_id, func.count(BoxEvent.id)),
            user,
            BoxEvent.warehouse_id,
        )
        .where(
            BoxEvent.event_type == BoxEventType.created,
            BoxEvent.occurred_at >= midnight,
            BoxEvent.occurred_at < tomorrow,
        )
        .group_by(BoxEvent.warehouse_id)
    ).all()
    received_today = {wid: int(c) for wid, c in received_today_rows}

    returned_today_rows = db.execute(
        apply_warehouse_filter(
            select(BoxEvent.warehouse_id, func.count(BoxEvent.id)),
            user,
            BoxEvent.warehouse_id,
        )
        .where(
            BoxEvent.event_type == BoxEventType.returned,
            BoxEvent.occurred_at >= midnight,
            BoxEvent.occurred_at < tomorrow,
        )
        .group_by(BoxEvent.warehouse_id)
    ).all()
    returned_today = {wid: int(c) for wid, c in returned_today_rows}

    # "Box completion" = a box left the ``processing`` state today (into
    # either ``incomplete`` when emptied early or ``ready_to_return``
    # when finished cleanly). We look at audit events rather than the
    # current ``status`` column because a box can complete and later be
    # returned in the same day; the audit row is the canonical record.
    completed_today_rows = db.execute(
        apply_warehouse_filter(
            select(BoxEvent.warehouse_id, func.count(BoxEvent.id)),
            user,
            BoxEvent.warehouse_id,
        )
        .where(
            BoxEvent.event_type == BoxEventType.status_changed,
            BoxEvent.from_status == BoxStatus.processing,
            BoxEvent.to_status.in_(list(UNAVAILABLE_STATUSES)),
            BoxEvent.occurred_at >= midnight,
            BoxEvent.occurred_at < tomorrow,
        )
        .group_by(BoxEvent.warehouse_id)
    ).all()
    completed_today = {wid: int(c) for wid, c in completed_today_rows}

    open_alert_rows = db.execute(
        apply_warehouse_filter(
            select(Alert.warehouse_id, func.count(Alert.id)),
            user,
            Alert.warehouse_id,
        )
        .where(Alert.resolved_at.is_(None))
        .group_by(Alert.warehouse_id)
    ).all()
    open_alerts = {wid: int(c) for wid, c in open_alert_rows}

    # Productivity numbers come from the same helpers the standalone
    # /productivity endpoints use, so the dashboard cards always agree
    # with the dedicated page.
    today_local = _today_in_app_tz()
    productivity_today = daily_summary(db, user=user, on_date=today_local)
    productivity_week = weekly_summary(db, user=user, week_start=today_local)

    summaries: list[WarehouseSummary] = []
    total_active = 0
    total_available = 0
    total_unavailable = 0
    total_completed = 0
    total_open_alerts = 0
    for wh in warehouses:
        status_counts = by_warehouse_status.get(wh.id, {})
        full = {s: int(status_counts.get(s, 0)) for s in BoxStatus}
        available = sum(full[s] for s in AVAILABLE_STATUSES)
        unavailable = sum(full[s] for s in UNAVAILABLE_STATUSES)
        inventory = sum(full[s] for s in ACTIVE_STATUSES)
        ready_to_return = full[BoxStatus.ready_to_return]
        completed = completed_today.get(wh.id, 0)
        per_day = round(completed * 24.0 / elapsed_hours, 2)
        total_active += inventory
        total_available += available
        total_unavailable += unavailable
        total_completed += completed
        opens = open_alerts.get(wh.id, 0)
        total_open_alerts += opens
        prod_today = productivity_today.get(wh.id)
        prod_week = productivity_week.get(wh.id)
        summaries.append(
            WarehouseSummary(
                warehouse_id=wh.id,
                name=wh.name,
                min_inventory=wh.min_inventory,
                max_capacity=wh.max_capacity,
                inventory=inventory,
                available_boxes=available,
                unavailable_boxes=unavailable,
                ready_to_return_boxes=ready_to_return,
                received_today=received_today.get(wh.id, 0),
                returned_today=returned_today.get(wh.id, 0),
                completed_today=completed,
                completed_per_day=per_day,
                counts_by_status=full,
                open_alerts=opens,
                productivity_today=(
                    _to_productivity_out(prod_today) if prod_today else None
                ),
                productivity_week=(
                    _to_productivity_out(prod_week) if prod_week else None
                ),
            )
        )

    return DashboardSummary(
        warehouses=summaries,
        total_active_boxes=total_active,
        total_available_boxes=total_available,
        total_unavailable_boxes=total_unavailable,
        total_completed_today=total_completed,
        total_open_alerts=total_open_alerts,
    )
