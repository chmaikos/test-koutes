"""Threshold evaluation and alert lifecycle management.

Hooked into every box mutation (so we react instantly) and also driven by
APScheduler every 60 seconds (defence in depth in case a mutation path skips
us). Sends Microsoft Graph email on transition into the "triggered" state and
publishes SSE events for the SPA banner.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.events import bus
from app.models.alerts import Alert, AlertType
from app.models.boxes import ACTIVE_STATUSES, Box
from app.models.warehouses import Warehouse
from app.services.graph_email import send_alert_email

logger = logging.getLogger("warehouse.alerts")


@dataclass
class _Eval:
    warehouse: Warehouse
    inventory: int


def _inventory_per_warehouse(db: Session) -> list[_Eval]:
    rows = db.execute(
        select(Warehouse, func.count(Box.id))
        .join(Box, Box.current_warehouse_id == Warehouse.id, isouter=True)
        .where((Box.status.in_(ACTIVE_STATUSES)) | (Box.id.is_(None)))
        .group_by(Warehouse.id)
        .order_by(Warehouse.id)
    ).all()
    out: list[_Eval] = []
    for warehouse, count in rows:
        out.append(_Eval(warehouse=warehouse, inventory=int(count or 0)))
    return out


def _open_alert(db: Session, warehouse_id: int, alert_type: AlertType) -> Alert | None:
    return db.scalar(
        select(Alert).where(
            Alert.warehouse_id == warehouse_id,
            Alert.type == alert_type,
            Alert.resolved_at.is_(None),
        )
    )


def _publish(event_type: str, payload: dict) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        bus.publish_threadsafe(event_type, payload)
        return
    loop.create_task(bus.publish(event_type, payload))


def _format_email_body(
    warehouse: Warehouse, alert_type: AlertType, value: int, threshold: int
) -> tuple[str, str]:
    if alert_type == AlertType.low_inventory:
        subject = f"[Warehouse] LOW inventory at {warehouse.name}"
        body = (
            f"<p>Warehouse <b>{warehouse.name}</b> inventory is "
            f"<b>{value}</b> (minimum: {threshold}).</p>"
        )
    else:
        subject = f"[Warehouse] MAX capacity reached at {warehouse.name}"
        body = (
            f"<p>Warehouse <b>{warehouse.name}</b> is at <b>{value}</b> boxes "
            f"(capacity: {threshold}).</p>"
        )
    return subject, body


def evaluate_alerts(db: Session) -> list[Alert]:
    """Reconcile the alert table against current inventory. Returns alerts that
    were *newly triggered* in this evaluation."""
    now = datetime.now(UTC)
    triggered: list[Alert] = []

    for ev in _inventory_per_warehouse(db):
        wh = ev.warehouse
        # --- low inventory ------------------------------------------------
        low_open = _open_alert(db, wh.id, AlertType.low_inventory)
        if ev.inventory < wh.min_inventory:
            if low_open is None:
                alert = Alert(
                    warehouse_id=wh.id,
                    type=AlertType.low_inventory,
                    threshold=wh.min_inventory,
                    value=ev.inventory,
                    triggered_at=now,
                )
                db.add(alert)
                db.flush()
                triggered.append(alert)
            else:
                low_open.value = ev.inventory
        else:
            if low_open is not None:
                low_open.resolved_at = now
                _publish(
                    "alert.resolved",
                    {"id": low_open.id, "warehouse_id": wh.id, "type": low_open.type.value},
                )

        # --- max capacity -------------------------------------------------
        max_open = _open_alert(db, wh.id, AlertType.max_capacity)
        if ev.inventory >= wh.max_capacity:
            if max_open is None:
                alert = Alert(
                    warehouse_id=wh.id,
                    type=AlertType.max_capacity,
                    threshold=wh.max_capacity,
                    value=ev.inventory,
                    triggered_at=now,
                )
                db.add(alert)
                db.flush()
                triggered.append(alert)
            else:
                max_open.value = ev.inventory
        else:
            if max_open is not None:
                max_open.resolved_at = now
                _publish(
                    "alert.resolved",
                    {"id": max_open.id, "warehouse_id": wh.id, "type": max_open.type.value},
                )

    db.commit()

    for alert in triggered:
        wh = db.get(Warehouse, alert.warehouse_id)
        if wh is None:
            continue
        subject, html = _format_email_body(wh, alert.type, alert.value, alert.threshold)
        if send_alert_email(subject=subject, html_body=html):
            alert.notified_at = datetime.now(UTC)
            db.commit()
        _publish(
            "alert.triggered",
            {
                "id": alert.id,
                "warehouse_id": alert.warehouse_id,
                "type": alert.type.value,
                "value": alert.value,
                "threshold": alert.threshold,
            },
        )
    return triggered


def evaluate_safe(db: Session) -> None:
    """Best-effort wrapper that never raises out of a request hook."""
    try:
        evaluate_alerts(db)
    except Exception:  # pragma: no cover - defensive
        logger.exception("alert evaluation failed")
