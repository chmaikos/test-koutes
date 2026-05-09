from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin, require_operator
from app.models.alerts import Alert, AlertNotification, AlertNotificationKind
from app.models.warehouses import Warehouse
from app.schemas.alerts import (
    AlertDetailOut,
    AlertNotificationOut,
    AlertOut,
    AlertRecipientsOut,
    AlertRecipientsRow,
    AlertTestEmailResult,
)
from app.services.acl import apply_warehouse_filter, can_access
from app.services.alert_email import EmailKind, render_for_alert
from app.services.alert_recipients import (
    escalation_recipients,
    primary_recipients,
)
from app.services.alerts import dispatch_notification

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(
    db: DbSession,
    user: CurrentUser,
    only_open: bool = Query(default=True),
) -> list[AlertOut]:
    stmt = apply_warehouse_filter(select(Alert), user, Alert.warehouse_id).order_by(
        Alert.triggered_at.desc()
    )
    if only_open:
        stmt = stmt.where(Alert.resolved_at.is_(None))
    rows = db.scalars(stmt.limit(200)).all()
    return [AlertOut.model_validate(a) for a in rows]


@router.get("/recipients", response_model=AlertRecipientsOut)
def list_recipients(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> AlertRecipientsOut:
    """Per-warehouse preview of who would be emailed if an alert opened now.

    Admin-only because it surfaces user emails. The escalation list is
    rendered alongside the primary list so admins can verify their own
    opt-out doesn't leave the safety net empty.
    """
    warehouses = db.scalars(select(Warehouse).order_by(Warehouse.id)).all()
    rows = [
        AlertRecipientsRow(
            warehouse_id=w.id,
            warehouse_name=w.name,
            primary=primary_recipients(db, w.id),
        )
        for w in warehouses
    ]
    return AlertRecipientsOut(
        warehouses=rows,
        escalation=escalation_recipients(db),
    )


@router.get("/{alert_id}", response_model=AlertDetailOut)
def get_alert(
    alert_id: int,
    db: DbSession,
    user: CurrentUser,
) -> AlertDetailOut:
    alert = db.get(Alert, alert_id)
    if alert is None or not can_access(user, alert.warehouse_id):
        raise HTTPException(status_code=404, detail="alert not found")
    notifications = db.scalars(
        select(AlertNotification)
        .where(AlertNotification.alert_id == alert.id)
        .order_by(AlertNotification.sent_at.asc(), AlertNotification.id.asc())
    ).all()
    return AlertDetailOut(
        alert=AlertOut.model_validate(alert),
        notifications=[
            AlertNotificationOut.model_validate(n) for n in notifications
        ],
    )


@router.post("/{alert_id}/ack", response_model=AlertOut)
def acknowledge(
    alert_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> AlertOut:
    alert = db.get(Alert, alert_id)
    # 404 (not 403) if the alert is in a warehouse outside the caller's ACL,
    # so we don't leak existence.
    if alert is None or not can_access(user, alert.warehouse_id):
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged_at = datetime.now(UTC)
    alert.acknowledged_by_user_id = user.id
    db.commit()
    db.refresh(alert)
    return AlertOut.model_validate(alert)


@router.post("/{alert_id}/test-email", response_model=AlertTestEmailResult)
def send_test_email(
    alert_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> AlertTestEmailResult:
    """Re-render the current alert email and send it to the caller.

    Admin-only and recorded as ``kind=test`` so it doesn't perturb the
    reminder/escalation cadence. Useful for sanity-checking that Graph
    is configured and that the rendered HTML looks right.
    """
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    # Even admins can use the test endpoint only against alerts they
    # could otherwise see -- in the multi-tenant future this protects us
    # from leaking warehouse names through the rendered subject.
    if not can_access(user, alert.warehouse_id):
        raise HTTPException(status_code=404, detail="alert not found")
    if not user.email:
        raise HTTPException(status_code=400, detail="caller has no email on file")
    record = dispatch_notification(
        db,
        alert=alert,
        kind=AlertNotificationKind.test,
        recipients=[user.email],
    )
    # Surface a 200 with the audit row even when Graph rejected the send;
    # admins want to see "we tried, here's why it failed" rather than a
    # generic 500.
    rendered = render_for_alert(db, kind=EmailKind.test, alert=alert)
    return AlertTestEmailResult(
        ok=record.ok,
        error=record.error,
        recipients=[user.email],
        subject=rendered.subject if rendered else "",
    )
