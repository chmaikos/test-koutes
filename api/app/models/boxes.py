from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.lots import Lot

if TYPE_CHECKING:
    from app.models.box_files import BoxFile
    from app.models.pallets import Pallet


class BoxStatus(str, enum.Enum):
    quarantined = "quarantined"
    received = "received"
    processing = "processing"
    incomplete = "incomplete"
    ready_to_return = "ready_to_return"
    returned = "returned"


# Functional groupings. ``available`` covers boxes that still have stuff to
# work with (closed = ``received``, open = ``processing``); ``unavailable``
# covers boxes that no longer have stuff but still occupy the warehouse
# (open-but-empty = ``incomplete``, closed-and-done = ``ready_to_return``).
# These two tuples are the basis of the dashboard's "available vs min" /
# "unavailable vs max" cards and of the alert thresholds; keeping them in
# the model means every downstream caller (alerts, dashboard, exports) gets
# the same answer for "is this box currently inventory?".
AVAILABLE_STATUSES: tuple[BoxStatus, ...] = (
    BoxStatus.received,
    BoxStatus.processing,
)
UNAVAILABLE_STATUSES: tuple[BoxStatus, ...] = (
    BoxStatus.incomplete,
    BoxStatus.ready_to_return,
)
OCCUPYING_STATUSES: tuple[BoxStatus, ...] = (
    BoxStatus.quarantined,
    *AVAILABLE_STATUSES,
    *UNAVAILABLE_STATUSES,
)
# Kept as a compatibility alias for callers that use "active" to mean a box
# that has not physically left the warehouse.
ACTIVE_STATUSES: tuple[BoxStatus, ...] = OCCUPYING_STATUSES


class BoxEventType(str, enum.Enum):
    created = "created"
    moved = "moved"
    lot_reassigned = "lot_reassigned"
    status_changed = "status_changed"
    returned = "returned"
    archived = "archived"
    restored = "restored"
    pallet_assigned = "pallet_assigned"
    pallet_unassigned = "pallet_unassigned"


class Box(Base):
    __tablename__ = "boxes"
    # ``box_number`` is unique only within a lot: the same number can show
    # up across different lots (a real-world warehouse routinely reuses
    # numbers like 001..050 for every fresh lot). The composite constraint
    # ``uq_boxes_lot_box_number`` enforces that at the database level so
    # racing inserts can't slip a duplicate past the application check.
    __table_args__ = (
        Index("ix_boxes_status_warehouse", "status", "current_warehouse_id"),
        UniqueConstraint("lot_id", "box_number", name="uq_boxes_lot_box_number"),
        UniqueConstraint("id", "lot_id", name="uq_boxes_id_lot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    box_number: Mapped[str] = mapped_column(String(64), index=True)
    lot_id: Mapped[int] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    lot_record: Mapped[Lot] = relationship(back_populates="boxes", lazy="joined")
    pallet_id: Mapped[int | None] = mapped_column(
        ForeignKey("pallets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pallet: Mapped[Pallet | None] = relationship("Pallet", back_populates="boxes")
    files: Mapped[list[BoxFile]] = relationship(
        back_populates="box",
        order_by="BoxFile.position",
        lazy="selectin",
        passive_deletes=True,
        overlaps="files,lot",
    )
    contents: Mapped[str | None] = mapped_column(Text)
    current_warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[BoxStatus] = mapped_column(
        Enum(BoxStatus, name="box_status"), nullable=False, default=BoxStatus.received
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archive_reason: Mapped[str | None] = mapped_column(Text)
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

    @property
    def lot_name(self) -> str:
        """Current display name; this is derived from the authoritative lot row."""
        return self.lot_record.name

    @hybrid_property
    def lot(self) -> str:
        """Compatibility accessor for API schemas and existing read queries."""
        return self.lot_name

    @lot.inplace.expression
    @classmethod
    def _lot_expression(cls):
        return select(Lot.name).where(Lot.id == cls.lot_id).scalar_subquery()

    @property
    def pallet_number(self) -> str | None:
        return self.pallet.pallet_number if self.pallet is not None else None

    @property
    def active_files(self) -> list[BoxFile]:
        if (
            self.archived_at is not None
            or self.lot_record.merged_into_lot_id is not None
        ):
            return []
        return [file for file in self.files if file.archived_at is None]

    @property
    def active_file_count(self) -> int:
        return len(self.active_files)

    @property
    def archived_file_count(self) -> int:
        return len(self.files) - self.active_file_count

    @property
    def file_count(self) -> int:
        return len(self.files)


class BoxEvent(Base):
    __tablename__ = "box_events"
    __table_args__ = (
        Index("ix_box_events_warehouse_occurred", "warehouse_id", "occurred_at"),
        Index(
            "ix_box_events_warehouse_type_occurred",
            "warehouse_id",
            "event_type",
            "occurred_at",
        ),
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
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default="{}"
    )


# helper imported by routers/services
__all__ = [
    "Box",
    "BoxEvent",
    "BoxEventType",
    "BoxStatus",
    "ACTIVE_STATUSES",
    "AVAILABLE_STATUSES",
    "OCCUPYING_STATUSES",
    "UNAVAILABLE_STATUSES",
]
