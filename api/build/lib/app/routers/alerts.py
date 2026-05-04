from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_operator
from app.models.alerts import Alert
from app.schemas.alerts import AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(
    db: DbSession,
    user: CurrentUser,
    only_open: bool = Query(default=True),
) -> list[AlertOut]:
    stmt = select(Alert).order_by(Alert.triggered_at.desc())
    if only_open:
        stmt = stmt.where(Alert.resolved_at.is_(None))
    rows = db.scalars(stmt.limit(200)).all()
    return [AlertOut.model_validate(a) for a in rows]


@router.post("/{alert_id}/ack", response_model=AlertOut)
def acknowledge(
    alert_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_operator)],
) -> AlertOut:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged_at = datetime.now(UTC)
    alert.acknowledged_by_user_id = user.id
    db.commit()
    db.refresh(alert)
    return AlertOut.model_validate(alert)
