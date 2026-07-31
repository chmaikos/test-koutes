from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class BoxRequestDirection(str, enum.Enum):
    inbound = "inbound"
    return_ = "return"


class BoxRequestOrigin(str, enum.Enum):
    workflow = "workflow"
    xlsx_import = "xlsx_import"
    manual_entry = "manual_entry"
    legacy_backfill = "legacy_backfill"


class BoxRequestStatus(str, enum.Enum):
    submitted = "submitted"
    approved = "approved"
    in_transit = "in_transit"
    completed = "completed"
    rejected = "rejected"
    cancelled = "cancelled"


ACTIVE_REQUEST_STATUSES: tuple[BoxRequestStatus, ...] = (
    BoxRequestStatus.submitted,
    BoxRequestStatus.approved,
    BoxRequestStatus.in_transit,
)


class BoxRequestEventType(str, enum.Enum):
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    in_transit = "in_transit"
    completed = "completed"
    cancelled = "cancelled"
    document_uploaded = "document_uploaded"


class BoxRequestDocumentType(str, enum.Enum):
    delivery_note = "delivery_note"
    return_note = "return_note"
    other = "other"


class BoxRequest(Base):
    __tablename__ = "box_requests"
    __table_args__ = (
        Index("ix_box_requests_warehouse_status", "warehouse_id", "status"),
        Index("ix_box_requests_requester_created", "requester_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[BoxRequestDirection] = mapped_column(
        Enum(
            BoxRequestDirection,
            name="box_request_direction",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BoxRequestStatus] = mapped_column(
        Enum(BoxRequestStatus, name="box_request_status"),
        nullable=False,
        default=BoxRequestStatus.submitted,
    )
    requester_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    source_inbound_request_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "box_requests.id",
            name="fk_box_requests_source_inbound_request_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        index=True,
    )
    origin: Mapped[BoxRequestOrigin] = mapped_column(
        Enum(
            BoxRequestOrigin,
            name="box_request_origin",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=BoxRequestOrigin.workflow,
    )

    suggestion_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_inventory: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_inbound: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    eligible_return: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_received_quantity: Mapped[int | None] = mapped_column(Integer)
    variance_quantity: Mapped[int | None] = mapped_column(Integer)

    rejection_reason: Mapped[str | None] = mapped_column(Text)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    discrepancy_reason: Mapped[str | None] = mapped_column(Text)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    in_transit_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_transit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    items: Mapped[list[BoxRequestItem]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )
    documents: Mapped[list[BoxRequestDocument]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )


class BoxRequestItem(Base):
    __tablename__ = "box_request_items"
    __table_args__ = (
        UniqueConstraint("request_id", "position", name="uq_box_request_items_position"),
        Index("ix_box_request_items_box_id", "box_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    box_id: Mapped[int | None] = mapped_column(
        ForeignKey("boxes.id", ondelete="RESTRICT"), nullable=True
    )
    lot: Mapped[str | None] = mapped_column(String(64))
    box_number: Mapped[str | None] = mapped_column(String(64))
    contents: Mapped[str | None] = mapped_column(String(200))

    request: Mapped[BoxRequest] = relationship(back_populates="items")


class BoxRequestEvent(Base):
    __tablename__ = "box_request_events"
    __table_args__ = (
        Index("ix_box_request_events_request_occurred", "request_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[BoxRequestEventType] = mapped_column(
        Enum(BoxRequestEventType, name="box_request_event_type"), nullable=False
    )
    from_status: Mapped[BoxRequestStatus | None] = mapped_column(
        Enum(BoxRequestStatus, name="box_request_status", create_type=False)
    )
    to_status: Mapped[BoxRequestStatus | None] = mapped_column(
        Enum(BoxRequestStatus, name="box_request_status", create_type=False)
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BoxRequestDocument(Base):
    __tablename__ = "box_request_documents"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_box_request_documents_object_key"),
        Index("ix_box_request_documents_request_type", "request_id", "document_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[BoxRequestDocumentType] = mapped_column(
        Enum(BoxRequestDocumentType, name="box_request_document_type"), nullable=False
    )
    erp_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped[BoxRequest] = relationship(back_populates="documents")


__all__ = [
    "ACTIVE_REQUEST_STATUSES",
    "BoxRequest",
    "BoxRequestDirection",
    "BoxRequestDocument",
    "BoxRequestDocumentType",
    "BoxRequestEvent",
    "BoxRequestEventType",
    "BoxRequestItem",
    "BoxRequestStatus",
]
