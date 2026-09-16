from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AlertType(str, enum.Enum):
    low_inventory = "low_inventory"
    max_capacity = "max_capacity"
    # Leading indicators -- the warehouse is approaching the hard threshold
    # but hasn't crossed it yet. Useful as a heads-up to operators.
    near_capacity = "near_capacity"
    near_low_inventory = "near_low_inventory"
    # Preserved for historical rows only. New incidents are never evaluated.
    box_stuck = "box_stuck"


class AlertNotificationKind(str, enum.Enum):
    """Why an alert email was sent.

    Only ``triggered`` and admin-initiated ``test`` are active. The other
    values remain readable for historical audit rows.
    """

    triggered = "triggered"
    reminder = "reminder"
    escalated = "escalated"
    resolved = "resolved"
    test = "test"


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_open_warehouse_type",
            "warehouse_id",
            "type",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
            sqlite_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, name="alert_type"), nullable=False
    )
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Short-lived durable claim for opening-email dispatch. A timestamp,
    # rather than a boolean, lets another scheduler recover abandoned work.
    email_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Compatibility field for historical escalation state; no longer written.
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertNotification(Base):
    """Audit log of every email we attempt to send for an alert."""

    __tablename__ = "alert_notifications"
    __table_args__ = (
        Index("ix_alert_notifications_alert_sent", "alert_id", "sent_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[AlertNotificationKind] = mapped_column(
        Enum(AlertNotificationKind, name="alert_notification_kind"), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Comma-separated recipients as actually used for this send. Storing them
    # opaquely (rather than per-row) keeps the audit table flat; the list is
    # short and we never need to query individual recipients.
    recipients: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)
