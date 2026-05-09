from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.alerts import AlertNotificationKind, AlertType


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    type: AlertType
    threshold: int
    value: int
    triggered_at: datetime
    notified_at: datetime | None
    resolved_at: datetime | None
    acknowledged_at: datetime | None
    escalated_at: datetime | None = None


class AlertNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    kind: AlertNotificationKind
    sent_at: datetime
    recipients: str
    ok: bool
    error: str | None = None


class AlertDetailOut(BaseModel):
    """Single-alert payload used by the alert detail page."""

    alert: AlertOut
    notifications: list[AlertNotificationOut] = Field(default_factory=list)


class AlertRecipientsRow(BaseModel):
    warehouse_id: int
    warehouse_name: str
    primary: list[str] = Field(default_factory=list)


class AlertRecipientsOut(BaseModel):
    """Admin-only preview of the recipient resolver output."""

    warehouses: list[AlertRecipientsRow] = Field(default_factory=list)
    escalation: list[str] = Field(default_factory=list)


class AlertTestEmailResult(BaseModel):
    """Outcome of POST /alerts/{id}/test-email."""

    ok: bool
    error: str | None = None
    recipients: list[str] = Field(default_factory=list)
    subject: str = ""
