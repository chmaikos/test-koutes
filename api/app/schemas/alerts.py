from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.alerts import AlertType


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
