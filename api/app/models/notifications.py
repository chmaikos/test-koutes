from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RequestNotificationKind(str, enum.Enum):
    staged = "staged"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    preparation_started = "preparation_started"
    ready_for_transport = "ready_for_transport"
    document_needed = "document_needed"
    document_uploaded = "document_uploaded"
    quarantine_released = "quarantine_released"
    quarantine_rejected = "quarantine_rejected"
    scheduled = "scheduled"
    transport_started = "transport_started"
    acceptance_required = "acceptance_required"
    confirmation_received = "confirmation_received"
    hold_started = "hold_started"
    resumed = "resumed"
    rescheduled = "rescheduled"
    failed_delivery = "failed_delivery"
    transport_retry = "transport_retry"
    comment_added = "comment_added"
    reservation_cancelled = "reservation_cancelled"
    overdue = "overdue"
    assignment_changed = "assignment_changed"
    coordination_changed = "coordination_changed"
    attachment_uploaded = "attachment_uploaded"


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_in_app_notifications_key"),
        Index("ix_in_app_notifications_user_created", "user_id", "created_at"),
        Index("ix_in_app_notifications_user_read", "user_id", "read_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), index=True
    )
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    deep_link: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RequestEmailOutbox(Base):
    """Durable request-email work item and delivery audit record."""

    __tablename__ = "request_email_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_request_email_outbox_key"),
        Index("ix_request_email_outbox_pending", "sent_at", "available_at"),
        Index("ix_request_email_outbox_request", "request_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False
    )
    recipient_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "InAppNotification",
    "RequestEmailOutbox",
    "RequestNotificationKind",
]
