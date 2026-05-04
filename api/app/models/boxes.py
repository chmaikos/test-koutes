from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BoxStatus(str, enum.Enum):
    received = "received"
    in_progress = "in_progress"
    processing_complete = "processing_complete"
    ready_to_return = "ready_to_return"
    returned = "returned"


ACTIVE_STATUSES: tuple[BoxStatus, ...] = (
    BoxStatus.received,
    BoxStatus.in_progress,
    BoxStatus.processing_complete,
    BoxStatus.ready_to_return,
)


class BoxEventType(str, enum.Enum):
    created = "created"
    moved = "moved"
    status_changed = "status_changed"
    returned = "returned"


class Box(Base):
    __tablename__ = "boxes"
    __table_args__ = (
        Index("ix_boxes_status_warehouse", "status", "current_warehouse_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    box_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    current_warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[BoxStatus] = mapped_column(
        Enum(BoxStatus, name="box_status"), nullable=False, default=BoxStatus.received
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class BoxEvent(Base):
    __tablename__ = "box_events"
    __table_args__ = (
        Index("ix_box_events_warehouse_occurred", "warehouse_id", "occurred_at"),
        Index("ix_box_events_box_occurred", "box_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    box_id: Mapped[int] = mapped_column(
        ForeignKey("boxes.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[BoxEventType] = mapped_column(
        Enum(BoxEventType, name="box_event_type"), nullable=False
    )
    from_status: Mapped[BoxStatus | None] = mapped_column(
        Enum(BoxStatus, name="box_status", create_type=False)
    )
    to_status: Mapped[BoxStatus | None] = mapped_column(
        Enum(BoxStatus, name="box_status", create_type=False)
    )
    from_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    to_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)


# helper imported by routers/services
__all__ = ["Box", "BoxEvent", "BoxEventType", "BoxStatus", "ACTIVE_STATUSES"]
