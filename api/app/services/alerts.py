"""Alert lifecycle: detect, then dispatch.

The lifecycle is intentionally split in two:

1. :func:`evaluate_alerts` is *detect-only*. It opens new alert rows when
   a threshold is crossed, refreshes ``value`` on rows that are still open,
   and stamps ``resolved_at`` on rows whose underlying condition has
   cleared. It runs on every box mutation and on a 60-second timer, so it
   has to be cheap and side-effect free beyond DB writes.

2. :func:`dispatch_pending_notifications` runs on a separate, slower timer
   (5 min by default) and is responsible for everything email related:
   first ``triggered`` send, ``reminder`` cadence, one-shot ``escalated``,
   and the ``resolved`` close-out. Every attempt -- success or failure --
   writes an :class:`AlertNotification` row, which is what gates idempotency
   and powers the audit timeline on the alert detail page.

We keep both halves in this module because they share the SSE publishing
helper and the ``_open_alert``/``_inventory_per_warehouse`` queries.
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.events import bus
from app.models.alerts import (
    Alert,
    AlertNotification,
    AlertNotificationKind,
    AlertType,
)
from app.models.boxes import (
    AVAILABLE_STATUSES,
    UNAVAILABLE_STATUSES,
    Box,
    BoxStatus,
)
from app.models.warehouses import Warehouse
from app.services.alert_email import EmailKind, render_for_alert
from app.services.alert_recipients import (
    escalation_recipients,
    primary_recipients,
)
from app.services.graph_email import send_alert_email

logger = logging.getLogger("warehouse.alerts")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass
class _Eval:
    """One warehouse's per-status accounting used by every alert trigger.

    Splitting into ``available`` / ``unavailable`` matters because the
    business rules diverge: low_inventory / near_low_inventory ask
    "do we have enough boxes ready to be used" (available), while
    max_capacity / near_capacity ask "are we accumulating too many
    occupied-but-not-usable boxes" (unavailable). The dashboard
    surfaces the same two numbers so operators and the alert engine
    can never disagree about the state of a warehouse.
    """

    warehouse: Warehouse
    available: int
    unavailable: int

    @property
    def inventory(self) -> int:
        """Total in-warehouse boxes (everything but ``returned``)."""
        return self.available + self.unavailable


def _inventory_per_warehouse(db: Session) -> list[_Eval]:
    """Return one ``_Eval`` per warehouse with the available/unavailable
    split materialised in a single query. Warehouses with no boxes still
    appear (zeroed) so per-warehouse alerts (e.g. low_inventory on an
    empty warehouse) can fire from a cold start."""

    available_case = func.sum(
        case(
            (Box.status.in_(AVAILABLE_STATUSES), 1),
            else_=0,
        )
    )
    unavailable_case = func.sum(
        case(
            (Box.status.in_(UNAVAILABLE_STATUSES), 1),
            else_=0,
        )
    )
    rows = db.execute(
        select(Warehouse, available_case, unavailable_case)
        .join(Box, Box.current_warehouse_id == Warehouse.id, isouter=True)
        .group_by(Warehouse.id)
        .order_by(Warehouse.id)
    ).all()
    out: list[_Eval] = []
    for warehouse, available, unavailable in rows:
        out.append(
            _Eval(
                warehouse=warehouse,
                available=int(available or 0),
                unavailable=int(unavailable or 0),
            )
        )
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


def _stuck_boxes_per_warehouse(
    db: Session, *, older_than: datetime
) -> dict[int, int]:
    """Count boxes that have been in ``received`` for too long, by warehouse.

    A box counts as stuck if its ``received_at`` is older than
    ``older_than`` (or if it has no ``received_at`` but was created before
    that, as a defensive fallback for legacy rows). Boxes that have moved
    past ``received`` (into ``processing`` / ``incomplete`` / ...) are
    skipped regardless of how long they've been there -- ageing inside
    the open-box pipeline is intentionally out of scope for this alert
    and would need its own threshold to be meaningful.
    """
    rows = db.execute(
        select(Box.current_warehouse_id, func.count(Box.id))
        .where(
            Box.status == BoxStatus.received,
            and_(
                # received_at is the canonical "started waiting" timestamp;
                # when missing fall back to created_at so we don't lose
                # rows imported from a system that didn't fill it in.
                func.coalesce(Box.received_at, Box.created_at) <= older_than,
            ),
        )
        .group_by(Box.current_warehouse_id)
    ).all()
    return {int(wid): int(count) for wid, count in rows}


def _reconcile_alert(
    db: Session,
    *,
    warehouse_id: int,
    alert_type: AlertType,
    triggered_now: bool,
    value: int,
    threshold: int,
    now: datetime,
    triggered_out: list[Alert],
    resolved_out: list[Alert],
) -> None:
    """Open / refresh / close a single (warehouse, alert_type) pair.

    Centralises the open/close bookkeeping so each trigger family can be
    expressed as just "is the condition true right now, and at what
    value?". Pushes new alerts onto ``triggered_out`` and stamps closed
    alerts onto ``resolved_out`` for the caller to publish over SSE.
    """
    open_row = _open_alert(db, warehouse_id, alert_type)
    if triggered_now:
        if open_row is None:
            alert = Alert(
                warehouse_id=warehouse_id,
                type=alert_type,
                threshold=threshold,
                value=value,
                triggered_at=now,
            )
            db.add(alert)
            db.flush()
            triggered_out.append(alert)
        else:
            open_row.value = value
            # Refresh threshold so a subsequent warehouse-config change
            # is reflected on the existing open alert.
            open_row.threshold = threshold
    else:
        if open_row is not None:
            open_row.resolved_at = now
            resolved_out.append(open_row)


def evaluate_alerts(db: Session) -> list[Alert]:
    """Reconcile the alert table against current inventory.

    Detect-only -- this function never sends email. It returns the alerts
    that were *newly opened* in this evaluation so callers that want
    immediate feedback (e.g. tests) can introspect them, but the
    background dispatcher is what actually delivers the email.

    SSE events are still published here so the SPA's banner reacts in
    real time (the SPA never had to wait for the email path anyway).
    """
    settings = get_settings()
    now = datetime.now(UTC)
    triggered: list[Alert] = []
    resolved: list[Alert] = []

    # Box-stuck counts are a per-warehouse aggregate; pull them once so
    # the per-warehouse loop below can look them up in O(1).
    stuck_counts: dict[int, int] = {}
    if settings.box_stuck_threshold_days > 0:
        cutoff = now - timedelta(days=settings.box_stuck_threshold_days)
        stuck_counts = _stuck_boxes_per_warehouse(db, older_than=cutoff)

    for ev in _inventory_per_warehouse(db):
        wh = ev.warehouse

        # Low / near-low fire on the *available* supply -- the operator
        # cares whether there are enough usable boxes to pull from, not
        # how many are awaiting pickup. ``value`` stores the metric that
        # actually triggered so the email templates (which read it as the
        # current count) keep displaying the right number.
        low = ev.available < wh.min_inventory
        _reconcile_alert(
            db,
            warehouse_id=wh.id,
            alert_type=AlertType.low_inventory,
            triggered_now=low,
            value=ev.available,
            threshold=wh.min_inventory,
            now=now,
            triggered_out=triggered,
            resolved_out=resolved,
        )

        # Capacity / near-capacity fire on the *unavailable* backlog --
        # closed-and-done plus emptied-but-not-cleared. That's the
        # backlog actively eating warehouse space waiting for pickup or
        # downstream processing.
        over = ev.unavailable >= wh.max_capacity
        _reconcile_alert(
            db,
            warehouse_id=wh.id,
            alert_type=AlertType.max_capacity,
            triggered_now=over,
            value=ev.unavailable,
            threshold=wh.max_capacity,
            now=now,
            triggered_out=triggered,
            resolved_out=resolved,
        )

        # --- near capacity (heads-up) --------------------------------------
        # Only meaningful when the percentage is in (0, 100]; >= 100 is
        # functionally identical to max_capacity, so we skip the leading
        # indicator in that case to avoid stacking duplicate alerts.
        pct = settings.near_capacity_percent
        if 0 < pct < 100 and wh.max_capacity > 0:
            near_threshold = max(
                1, int(math.ceil(wh.max_capacity * pct / 100))
            )
            near_cap = (
                ev.unavailable >= near_threshold
                and ev.unavailable < wh.max_capacity
            )
            _reconcile_alert(
                db,
                warehouse_id=wh.id,
                alert_type=AlertType.near_capacity,
                triggered_now=near_cap,
                value=ev.unavailable,
                threshold=near_threshold,
                now=now,
                triggered_out=triggered,
                resolved_out=resolved,
            )

        # --- near low inventory (heads-up) ---------------------------------
        # Fires when available supply is within ``buffer`` of the minimum
        # but still above it; once we drop below min the low_inventory
        # alert takes over. We use min_inventory itself as the stored
        # threshold so the email body can talk in concrete numbers.
        buffer_n = settings.near_low_inventory_buffer
        if buffer_n > 0 and wh.min_inventory > 0:
            warning_floor = wh.min_inventory + buffer_n
            near_low = (
                ev.available >= wh.min_inventory
                and ev.available <= warning_floor
            )
            _reconcile_alert(
                db,
                warehouse_id=wh.id,
                alert_type=AlertType.near_low_inventory,
                triggered_now=near_low,
                value=ev.available,
                threshold=wh.min_inventory,
                now=now,
                triggered_out=triggered,
                resolved_out=resolved,
            )

        # --- box stuck -----------------------------------------------------
        if settings.box_stuck_threshold_days > 0:
            stuck_count = stuck_counts.get(wh.id, 0)
            _reconcile_alert(
                db,
                warehouse_id=wh.id,
                alert_type=AlertType.box_stuck,
                triggered_now=stuck_count > 0,
                value=stuck_count,
                threshold=settings.box_stuck_threshold_days,
                now=now,
                triggered_out=triggered,
                resolved_out=resolved,
            )

    db.commit()

    for alert in triggered:
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
    for alert in resolved:
        _publish(
            "alert.resolved",
            {
                "id": alert.id,
                "warehouse_id": alert.warehouse_id,
                "type": alert.type.value,
            },
        )
    return triggered


def evaluate_safe(db: Session) -> None:
    """Best-effort wrapper that never raises out of a request hook."""
    try:
        evaluate_alerts(db)
    except Exception:  # pragma: no cover - defensive
        logger.exception("alert evaluation failed")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_KIND_MAP: dict[AlertNotificationKind, EmailKind] = {
    AlertNotificationKind.triggered: EmailKind.triggered,
    AlertNotificationKind.reminder: EmailKind.reminder,
    AlertNotificationKind.escalated: EmailKind.escalated,
    AlertNotificationKind.resolved: EmailKind.resolved,
    AlertNotificationKind.test: EmailKind.test,
}


def _aware(value: datetime | None) -> datetime | None:
    """Ensure a DB-loaded datetime is timezone-aware.

    Our ``DateTime(timezone=True)`` columns round-trip through Postgres
    with tzinfo intact, but SQLite (used by the test suite) drops it on
    read. We normalise here so the dispatcher can compare against
    ``datetime.now(UTC)`` without a ``can't compare offset-naive`` error.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _last_notification_for(
    db: Session,
    alert_id: int,
    kinds: Iterable[AlertNotificationKind] | None = None,
) -> AlertNotification | None:
    """Return the most recent notification row for the alert.

    If ``kinds`` is provided, only those kinds are considered. Failed
    sends count: the dispatcher already retries triggered messages via
    ``alert.notified_at``, but for reminder/escalation idempotency we
    don't want to keep retrying a known-bad recipient list every tick.
    """
    stmt = (
        select(AlertNotification)
        .where(AlertNotification.alert_id == alert_id)
        .order_by(AlertNotification.sent_at.desc())
        .limit(1)
    )
    if kinds is not None:
        stmt = stmt.where(AlertNotification.kind.in_(list(kinds)))
    return db.scalar(stmt)


def dispatch_notification(
    db: Session,
    *,
    alert: Alert,
    kind: AlertNotificationKind,
    recipients: list[str],
) -> AlertNotification:
    """Render + send + audit a single notification.

    Always commits an :class:`AlertNotification` row -- with ``ok=False``
    and an ``error`` message when the send fails -- so the operator can
    tell the difference between "we never tried" and "we tried and failed".
    Returns the row for the caller's convenience.
    """
    email = render_for_alert(db, kind=_KIND_MAP[kind], alert=alert)
    if email is None:
        # Warehouse vanished mid-flight; record an audit row but don't try
        # to dispatch -- the alert is effectively orphaned and a human
        # will have to clean it up manually.
        record = AlertNotification(
            alert_id=alert.id,
            kind=kind,
            recipients=", ".join(recipients),
            ok=False,
            error="warehouse missing",
        )
        db.add(record)
        db.commit()
        return record

    if not recipients:
        record = AlertNotification(
            alert_id=alert.id,
            kind=kind,
            recipients="",
            ok=False,
            error="no recipients",
        )
        db.add(record)
        db.commit()
        return record

    # Plain-text body is rendered for the future audit log + as a fallback
    # for clients that strip HTML; Graph's sendMail is HTML-only today so
    # we only forward the html body.
    logger.debug("alert %s text body (%s):\n%s", alert.id, kind.value, email.text)
    ok, error = send_alert_email(
        subject=email.subject,
        html_body=email.html,
        to=recipients,
    )
    record = AlertNotification(
        alert_id=alert.id,
        kind=kind,
        recipients=", ".join(recipients),
        ok=ok,
        error=error,
    )
    db.add(record)
    db.commit()
    return record


def _send_triggered(db: Session, alert: Alert, now: datetime) -> None:
    recipients = primary_recipients(db, alert.warehouse_id)
    record = dispatch_notification(
        db,
        alert=alert,
        kind=AlertNotificationKind.triggered,
        recipients=recipients,
    )
    if record.ok:
        alert.notified_at = now
        db.commit()


def _send_reminder(db: Session, alert: Alert) -> None:
    recipients = primary_recipients(db, alert.warehouse_id)
    dispatch_notification(
        db,
        alert=alert,
        kind=AlertNotificationKind.reminder,
        recipients=recipients,
    )


def _send_escalation(db: Session, alert: Alert, now: datetime) -> None:
    recipients = escalation_recipients(db)
    record = dispatch_notification(
        db,
        alert=alert,
        kind=AlertNotificationKind.escalated,
        recipients=recipients,
    )
    if record.ok:
        # Stamp escalated_at even when send succeeded so we never re-send.
        # If the send failed we leave it null so the next tick retries.
        alert.escalated_at = now
        db.commit()


def _send_resolved(db: Session, alert: Alert) -> None:
    recipients = primary_recipients(db, alert.warehouse_id)
    dispatch_notification(
        db,
        alert=alert,
        kind=AlertNotificationKind.resolved,
        recipients=recipients,
    )


def dispatch_pending_notifications(db: Session) -> int:
    """Send any triggered/reminder/escalated/resolved emails that are due.

    Idempotent: each kind is gated either by a column on the alert
    (``notified_at`` for triggered, ``escalated_at`` for escalated) or by
    the ``alert_notifications`` audit table (reminder cadence, resolved
    one-shot). Returns the number of notifications actually attempted.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    sent = 0

    # 1. First-touch triggered email for any open alert that hasn't been
    #    successfully notified yet. We retry on every tick until it goes
    #    through; each attempt writes its own audit row.
    pending_triggered = db.scalars(
        select(Alert).where(
            Alert.notified_at.is_(None),
            Alert.resolved_at.is_(None),
        )
    ).all()
    for alert in pending_triggered:
        _send_triggered(db, alert, now)
        sent += 1

    # 2. Reminder cadence: open alerts whose last notification (any kind
    #    except 'test') is older than the configured window get a
    #    reminder. 0 hours disables.
    if settings.alert_reminder_hours > 0:
        cutoff = now - timedelta(hours=settings.alert_reminder_hours)
        open_alerts = db.scalars(
            select(Alert).where(
                Alert.resolved_at.is_(None),
                Alert.notified_at.is_not(None),
            )
        ).all()
        reminder_kinds = (
            AlertNotificationKind.triggered,
            AlertNotificationKind.reminder,
            AlertNotificationKind.escalated,
        )
        for alert in open_alerts:
            last = _last_notification_for(db, alert.id, kinds=reminder_kinds)
            if last is None:
                # Defensive: triggered email recorded notified_at but we
                # somehow have no audit row. Treat as overdue.
                notified = _aware(alert.notified_at)
                if notified is not None and notified <= cutoff:
                    _send_reminder(db, alert)
                    sent += 1
                continue
            sent_at = _aware(last.sent_at)
            if sent_at is not None and sent_at <= cutoff:
                _send_reminder(db, alert)
                sent += 1

    # 3. Escalation: any open alert older than the escalation window that
    #    we haven't escalated yet gets a single admin-only mail. The
    #    one-shot guarantee is enforced by the escalated_at column.
    if settings.alert_escalation_hours > 0:
        cutoff = now - timedelta(hours=settings.alert_escalation_hours)
        candidates = db.scalars(
            select(Alert).where(
                Alert.resolved_at.is_(None),
                Alert.escalated_at.is_(None),
                Alert.triggered_at <= cutoff,
            )
        ).all()
        for alert in candidates:
            # Defensive double-check on SQLite where the WHERE clause may
            # apply to naive timestamps; the in-Python comparison normalises
            # tzinfo before deciding.
            triggered = _aware(alert.triggered_at)
            if triggered is None or triggered > cutoff:
                continue
            _send_escalation(db, alert, now)
            sent += 1

    # 4. Resolved close-out: alerts that have been stamped resolved but
    #    never had a 'resolved' email sent. This covers both the auto
    #    close-out from evaluate_alerts and any manual resolutions. We
    #    look for the absence of a successful 'resolved' row to keep
    #    failed sends retriable; we cap retries by also bailing out if a
    #    failed row exists newer than 1 hour, to avoid hammering Graph
    #    on a permanent misconfiguration.
    resolved_candidates = db.scalars(
        select(Alert).where(Alert.resolved_at.is_not(None))
    ).all()
    retry_floor = now - timedelta(hours=1)
    for alert in resolved_candidates:
        latest_resolved = _last_notification_for(
            db, alert.id, kinds=(AlertNotificationKind.resolved,)
        )
        if latest_resolved is not None and latest_resolved.ok:
            continue
        if latest_resolved is not None:
            sent_at = _aware(latest_resolved.sent_at)
            if sent_at is not None and sent_at >= retry_floor:
                # We tried and failed recently; back off until next tick.
                continue
        _send_resolved(db, alert)
        sent += 1

    return sent


def dispatch_safe(db: Session) -> None:
    """Best-effort wrapper used by the scheduler tick."""
    try:
        dispatch_pending_notifications(db)
    except Exception:  # pragma: no cover - defensive
        logger.exception("alert dispatch failed")
