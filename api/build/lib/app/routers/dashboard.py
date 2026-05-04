from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models.alerts import Alert
from app.models.boxes import ACTIVE_STATUSES, Box, BoxEvent, BoxEventType, BoxStatus
from app.models.warehouses import Warehouse
from app.schemas.dashboard import DashboardSummary, WarehouseSummary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def summary(db: DbSession, user: CurrentUser) -> DashboardSummary:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = midnight + timedelta(days=1)

    warehouses = db.scalars(select(Warehouse).order_by(Warehouse.id)).all()

    status_rows = db.execute(
        select(Box.current_warehouse_id, Box.status, func.count(Box.id))
        .group_by(Box.current_warehouse_id, Box.status)
    ).all()
    by_warehouse_status: dict[int, dict[BoxStatus, int]] = {}
    for wid, st, c in status_rows:
        by_warehouse_status.setdefault(wid, {})[st] = int(c)

    received_today_rows = db.execute(
        select(BoxEvent.warehouse_id, func.count(BoxEvent.id))
        .where(
            BoxEvent.event_type == BoxEventType.created,
            BoxEvent.occurred_at >= midnight,
            BoxEvent.occurred_at < tomorrow,
        )
        .group_by(BoxEvent.warehouse_id)
    ).all()
    received_today = {wid: int(c) for wid, c in received_today_rows}

    returned_today_rows = db.execute(
        select(BoxEvent.warehouse_id, func.count(BoxEvent.id))
        .where(
            BoxEvent.event_type == BoxEventType.returned,
            BoxEvent.occurred_at >= midnight,
            BoxEvent.occurred_at < tomorrow,
        )
        .group_by(BoxEvent.warehouse_id)
    ).all()
    returned_today = {wid: int(c) for wid, c in returned_today_rows}

    open_alert_rows = db.execute(
        select(Alert.warehouse_id, func.count(Alert.id))
        .where(Alert.resolved_at.is_(None))
        .group_by(Alert.warehouse_id)
    ).all()
    open_alerts = {wid: int(c) for wid, c in open_alert_rows}

    summaries: list[WarehouseSummary] = []
    total_active = 0
    total_open_alerts = 0
    for wh in warehouses:
        status_counts = by_warehouse_status.get(wh.id, {})
        full = {s: int(status_counts.get(s, 0)) for s in BoxStatus}
        inventory = sum(full[s] for s in ACTIVE_STATUSES)
        total_active += inventory
        opens = open_alerts.get(wh.id, 0)
        total_open_alerts += opens
        summaries.append(
            WarehouseSummary(
                warehouse_id=wh.id,
                name=wh.name,
                min_inventory=wh.min_inventory,
                max_capacity=wh.max_capacity,
                inventory=inventory,
                received_today=received_today.get(wh.id, 0),
                returned_today=returned_today.get(wh.id, 0),
                counts_by_status=full,
                open_alerts=opens,
            )
        )

    return DashboardSummary(
        warehouses=summaries,
        total_active_boxes=total_active,
        total_open_alerts=total_open_alerts,
    )
