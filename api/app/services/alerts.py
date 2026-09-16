"""Alert detection and opening-email delivery.

Detection maintains alert lifecycle/history and publishes SSE events. Email
dispatch is deliberately narrower: each non-legacy incident is eligible only
for its opening ``triggered`` message, with at most five audited attempts.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, insert, literal, select, update
from sqlalchemy.exc import IntegrityError
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
from app.services.alert_recipients import primary_recipients
from app.services.graph_email import send_alert_email

logger = logging.getLogger("warehouse.alerts")

_MAX_TRIGGERED_ATTEMPTS = 5
_EMAIL_CLAIM_TIMEOUT = timedelta(minutes=15)
_UNKNOWN_TRANSPORT_RESULT = "transport result unknown; attempt reserved before send"


@dataclass(frozen=True)
class _EmailClaim:
    alert_id: int
    token: datetime


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
    quarantined: int

    @property
    def inventory(self) -> int:
        """Total in-warehouse boxes (everything but ``returned``)."""
        return self.available + self.unavailable + self.quarantined


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
    quarantined_case = func.sum(
        case(
            (Box.status == BoxStatus.quarantined, 1),
            else_=0,
        )
    )
    rows = db.execute(
        select(Warehouse, available_case, unavailable_case, quarantined_case)
        .join(
            Box,
            and_(
                Box.current_warehouse_id == Warehouse.id,
                Box.archived_at.is_(None),
            ),
            isouter=True,
        )
        .where(Warehouse.is_active.is_(True))
        .group_by(Warehouse.id)
        .order_by(Warehouse.id)
    ).all()
    out: list[_Eval] = []
    for warehouse, available, unavailable, quarantined in rows:
        out.append(
            _Eval(
                warehouse=warehouse,
                available=int(available or 0),
                unavailable=int(unavailable or 0),
                quarantined=int(quarantined or 0),
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
            try:
                # The partial unique index is the final arbiter when two
                # evaluators observe the condition concurrently. The
                # savepoint keeps a losing INSERT from poisoning the caller's
                # outer transaction.
                with db.begin_nested():
                    db.add(alert)
                    db.flush()
            except IntegrityError:
                open_row = _open_alert(db, warehouse_id, alert_type)
                if open_row is None:
                    raise
                open_row.value = value
                open_row.threshold = threshold
            else:
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
        over = ev.inventory >= wh.max_capacity
        _reconcile_alert(
            db,
            warehouse_id=wh.id,
            alert_type=AlertType.max_capacity,
            triggered_now=over,
            value=ev.inventory,
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
                ev.inventory >= near_threshold
                and ev.inventory < wh.max_capacity
            )
            _reconcile_alert(
                db,
                warehouse_id=wh.id,
                alert_type=AlertType.near_capacity,
                triggered_now=near_cap,
                value=ev.inventory,
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
    AlertNotificationKind.test: EmailKind.test,
}


def dispatch_notification(
    db: Session,
    *,
    alert: Alert,
    kind: AlertNotificationKind,
    recipients: list[str],
    commit: bool = True,
) -> AlertNotification:
    """Render + send + audit a single notification.

    This entry point is for admin-requested test mail. Opening delivery uses
    its dedicated reserve-before-transport flow below.
    """
    if alert.type == AlertType.box_stuck:
        raise ValueError("legacy box_stuck alerts cannot send email")
    if kind != AlertNotificationKind.test:
        raise ValueError(f"{kind.value} alert email is no longer active")

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
        if commit:
            db.commit()
        else:
            db.flush()
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
        if commit:
            db.commit()
        else:
            db.flush()
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
    if commit:
        db.commit()
    else:
        db.flush()
    return record


def _triggered_attempt_count(db: Session, alert_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(AlertNotification.id)).where(
                AlertNotification.alert_id == alert_id,
                AlertNotification.kind == AlertNotificationKind.triggered,
            )
        )
        or 0
    )


def _successful_triggered(
    db: Session, alert_id: int
) -> AlertNotification | None:
    return db.scalar(
        select(AlertNotification)
        .where(
            AlertNotification.alert_id == alert_id,
            AlertNotification.kind == AlertNotificationKind.triggered,
            AlertNotification.ok.is_(True),
        )
        .order_by(AlertNotification.sent_at.desc(), AlertNotification.id.desc())
        .limit(1)
    )


def _opening_eligibility(now: datetime):
    attempts = (
        select(func.count(AlertNotification.id))
        .where(
            AlertNotification.alert_id == Alert.id,
            AlertNotification.kind == AlertNotificationKind.triggered,
        )
        .correlate(Alert)
        .scalar_subquery()
    )
    successful = (
        select(AlertNotification.id)
        .where(
            AlertNotification.alert_id == Alert.id,
            AlertNotification.kind == AlertNotificationKind.triggered,
            AlertNotification.ok.is_(True),
        )
        .correlate(Alert)
        .exists()
    )
    stale_before = now - _EMAIL_CLAIM_TIMEOUT
    return (
        Alert.notified_at.is_(None),
        Alert.resolved_at.is_(None),
        Alert.type != AlertType.box_stuck,
        func.coalesce(attempts, 0) < _MAX_TRIGGERED_ATTEMPTS,
        ~successful,
        (
            Alert.email_claimed_at.is_(None)
            | (Alert.email_claimed_at < stale_before)
        ),
    )


def _opening_claim_select(now: datetime, exclude_ids: set[int]):
    conditions = _opening_eligibility(now)
    stmt = (
        select(Alert.id)
        .where(*conditions)
        .order_by(Alert.triggered_at, Alert.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if exclude_ids:
        stmt = stmt.where(Alert.id.notin_(exclude_ids))
    return stmt


def _claim_next_opening(
    db: Session, *, now: datetime, exclude_ids: set[int]
) -> _EmailClaim | None:
    """Claim one due incident without holding a row lock over Graph.

    PostgreSQL workers skip rows locked by another scheduler. The guarded
    UPDATE is also required for SQLite, whose SELECT compiler omits
    ``FOR UPDATE``; only one contender can replace a null/stale marker.
    """
    alert_id = db.scalar(_opening_claim_select(now, exclude_ids))
    if alert_id is None:
        db.commit()
        return None

    claimed = db.execute(
        update(Alert)
        .where(Alert.id == alert_id, *_opening_eligibility(now))
        .values(email_claimed_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    return _EmailClaim(alert_id=int(alert_id), token=now)


def _release_owned_claim(db: Session, claim: _EmailClaim) -> None:
    db.execute(
        update(Alert)
        .where(
            Alert.id == claim.alert_id,
            Alert.email_claimed_at == claim.token,
        )
        .values(email_claimed_at=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _triggered_reservation_insert(
    claim: _EmailClaim, recipients: list[str]
):
    attempts = (
        select(func.count(AlertNotification.id))
        .where(
            AlertNotification.alert_id == Alert.id,
            AlertNotification.kind == AlertNotificationKind.triggered,
        )
        .correlate(Alert)
        .scalar_subquery()
    )
    successful = (
        select(AlertNotification.id)
        .where(
            AlertNotification.alert_id == Alert.id,
            AlertNotification.kind == AlertNotificationKind.triggered,
            AlertNotification.ok.is_(True),
        )
        .correlate(Alert)
        .exists()
    )
    source = (
        select(
            Alert.id,
            literal(
                AlertNotificationKind.triggered,
                type_=AlertNotification.__table__.c.kind.type,
            ),
            literal(", ".join(recipients)),
            literal(False),
            literal(_UNKNOWN_TRANSPORT_RESULT),
        )
        .select_from(Alert)
        .where(
            Alert.id == claim.alert_id,
            Alert.email_claimed_at == claim.token,
            Alert.resolved_at.is_(None),
            Alert.notified_at.is_(None),
            Alert.type != AlertType.box_stuck,
            func.coalesce(attempts, 0) < _MAX_TRIGGERED_ATTEMPTS,
            ~successful,
        )
        .with_for_update()
    )
    return (
        insert(AlertNotification)
        .from_select(
            ["alert_id", "kind", "recipients", "ok", "error"],
            source,
        )
        .returning(AlertNotification.id)
    )


def _reserve_triggered_attempt(
    db: Session, claim: _EmailClaim, recipients: list[str]
) -> int | None:
    record_id = db.scalar(_triggered_reservation_insert(claim, recipients))
    db.commit()
    return int(record_id) if record_id is not None else None


def _dispatch_claimed_opening(db: Session, *, claim: _EmailClaim) -> bool:
    alert = db.scalar(
        select(Alert).where(
            Alert.id == claim.alert_id,
            Alert.email_claimed_at == claim.token,
        )
    )
    if alert is None:
        db.commit()
        return False

    successful = _successful_triggered(db, alert.id)
    if successful is not None:
        db.execute(
            update(Alert)
            .where(
                Alert.id == claim.alert_id,
                Alert.email_claimed_at == claim.token,
            )
            .values(
                notified_at=successful.sent_at,
                email_claimed_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
        return False
    if (
        alert.resolved_at is not None
        or alert.notified_at is not None
        or alert.type == AlertType.box_stuck
        or _triggered_attempt_count(db, alert.id) >= _MAX_TRIGGERED_ATTEMPTS
    ):
        _release_owned_claim(db, claim)
        return False

    # Rendering and recipient resolution are prerequisites, not transport
    # attempts. If either is unavailable, release the claim without writing
    # an audit row so a later scheduler tick can retry after configuration or
    # data is repaired.
    email = render_for_alert(
        db,
        kind=EmailKind.triggered,
        alert=alert,
    )
    if email is None:
        _release_owned_claim(db, claim)
        return False
    recipients = primary_recipients(db, alert.warehouse_id)
    if not recipients:
        _release_owned_claim(db, claim)
        return False

    # Reserve and commit quota before invoking the external transport. If the
    # process dies during Graph, this durable unknown-result row still counts
    # toward the hard five-invocation bound. The durable alert claim remains
    # set until Graph returns normally (or stale-claim recovery takes over).
    record_id = _reserve_triggered_attempt(db, claim, recipients)
    if record_id is None:
        # Ownership or eligibility changed while prerequisites were being
        # resolved. This worker must not send or clear another owner's claim.
        return False

    logger.debug(
        "alert %s text body (%s):\n%s",
        alert.id,
        AlertNotificationKind.triggered.value,
        email.text,
    )
    ok, error = send_alert_email(
        subject=email.subject,
        html_body=email.html,
        to=recipients,
    )
    db.execute(
        update(AlertNotification)
        .where(AlertNotification.id == record_id)
        .values(ok=ok, error=error)
        .execution_options(synchronize_session=False)
    )
    alert_values: dict[str, object] = {"email_claimed_at": None}
    if ok:
        alert_values["notified_at"] = datetime.now(UTC)
    db.execute(
        update(Alert)
        .where(
            Alert.id == claim.alert_id,
            Alert.email_claimed_at == claim.token,
        )
        .values(**alert_values)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return True


def dispatch_pending_notifications(db: Session) -> int:
    """Attempt each due opening email once during this scheduler run."""
    sent = 0
    processed: set[int] = set()
    while True:
        now = datetime.now(UTC)
        claim = _claim_next_opening(
            db, now=now, exclude_ids=processed
        )
        if claim is None:
            break
        processed.add(claim.alert_id)
        if _dispatch_claimed_opening(db, claim=claim):
            sent += 1
    return sent


def dispatch_safe(db: Session) -> None:
    """Best-effort wrapper used by the scheduler tick."""
    try:
        dispatch_pending_notifications(db)
    except Exception:  # pragma: no cover - defensive
        logger.exception("alert dispatch failed")
