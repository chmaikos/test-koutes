from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base

if TYPE_CHECKING:
    from app.models.box_files import BoxFile
    from app.models.lots import Lot
    from app.models.pallets import Pallet


class BoxRequestDirection(str, enum.Enum):
    inbound = "inbound"
    return_ = "return"


class BoxRequestOrigin(str, enum.Enum):
    workflow = "workflow"
    backorder = "backorder"
    return_reselection = "return_reselection"
    xlsx_import = "xlsx_import"
    manual_entry = "manual_entry"
    legacy_backfill = "legacy_backfill"


class BoxRequestStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    preparing = "preparing"
    ready_for_transport = "ready_for_transport"
    in_transit = "in_transit"
    awaiting_confirmation = "awaiting_confirmation"
    completed = "completed"
    rejected = "rejected"
    cancelled = "cancelled"


class BoxRequestPriority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


ACTIVE_REQUEST_STATUSES: tuple[BoxRequestStatus, ...] = (
    BoxRequestStatus.submitted,
    BoxRequestStatus.approved,
    BoxRequestStatus.preparing,
    BoxRequestStatus.ready_for_transport,
    BoxRequestStatus.in_transit,
    BoxRequestStatus.awaiting_confirmation,
)

BLOCKING_REQUEST_STATUSES: tuple[BoxRequestStatus, ...] = (
    BoxRequestStatus.draft,
    *ACTIVE_REQUEST_STATUSES,
)


class BoxRequestEventType(str, enum.Enum):
    draft_created = "draft_created"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    preparation_started = "preparation_started"
    ready_for_transport = "ready_for_transport"
    in_transit = "in_transit"
    awaiting_confirmation = "awaiting_confirmation"
    completed = "completed"
    partial_completion = "partial_completion"
    cancelled = "cancelled"
    document_uploaded = "document_uploaded"
    discrepancy_photo_uploaded = "discrepancy_photo_uploaded"
    assignment_changed = "assignment_changed"
    schedule_changed = "schedule_changed"
    coordination_changed = "coordination_changed"
    comment_added = "comment_added"
    attachment_uploaded = "attachment_uploaded"
    overdue = "overdue"
    hold_started = "hold_started"
    resumed = "resumed"
    rescheduled = "rescheduled"
    failed_delivery = "failed_delivery"
    transport_retry = "transport_retry"
    force_purge_adjusted = "force_purge_adjusted"


class BoxRequestExceptionKind(str, enum.Enum):
    hold = "hold"
    reschedule = "reschedule"
    failed_delivery = "failed_delivery"


class BoxRequestDocumentType(str, enum.Enum):
    delivery_note = "delivery_note"
    return_note = "return_note"
    other = "other"


class BoxRequestDiscrepancyType(str, enum.Enum):
    missing = "missing"
    unexpected = "unexpected"
    damaged = "damaged"
    wrong_lot = "wrong_lot"
    wrong_contents = "wrong_contents"
    rejected = "rejected"


class BoxRequestItemFileSnapshotKind(str, enum.Enum):
    tracked_file = "tracked_file"
    legacy_contents = "legacy_contents"


class BoxRequest(Base):
    __tablename__ = "box_requests"
    __table_args__ = (
        CheckConstraint(
            "(direction = 'return' AND target_warehouse_id IS NOT NULL) OR "
            "(direction = 'inbound' AND target_warehouse_id IS NULL)",
            name="ck_box_requests_direction_target_warehouse",
        ),
        Index("ix_box_requests_warehouse_status", "warehouse_id", "status"),
        Index("ix_box_requests_requester_created", "requester_user_id", "created_at"),
        Index("ix_box_requests_warehouse_submitted", "warehouse_id", "submitted_at"),
        Index(
            "ix_box_requests_assignee_status_sla",
            "assigned_mover_user_id",
            "status",
            "sla_deadline",
        ),
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
    target_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "warehouses.id",
            name="fk_box_requests_target_warehouse_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
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
    priority: Mapped[BoxRequestPriority] = mapped_column(
        Enum(
            BoxRequestPriority,
            name="box_request_priority",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=BoxRequestPriority.normal,
    )
    requested_date: Mapped[date | None] = mapped_column(Date)
    scheduled_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    assigned_mover_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    destination_contact: Mapped[str | None] = mapped_column(String(320))
    internal_location: Mapped[str | None] = mapped_column(String(320))
    special_handling_instructions: Mapped[str | None] = mapped_column(Text)
    source_inbound_request_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "box_requests.id",
            name="fk_box_requests_source_inbound_request_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        index=True,
    )
    parent_request_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "box_requests.id",
            name="fk_box_requests_parent_request_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        index=True,
    )
    root_request_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "box_requests.id",
            name="fk_box_requests_root_request_id",
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
    recommendation_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    actual_received_quantity: Mapped[int | None] = mapped_column(Integer)
    variance_quantity: Mapped[int | None] = mapped_column(Integer)

    rejection_reason: Mapped[str | None] = mapped_column(Text)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    discrepancy_reason: Mapped[str | None] = mapped_column(Text)
    completion_idempotency_key: Mapped[str | None] = mapped_column(String(120))
    receipt_restore_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    receipt_quarantine: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    receipt_document_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    preparing_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    ready_for_transport_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    in_transit_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    awaiting_confirmation_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preparing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_for_transport_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    in_transit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    awaiting_confirmation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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
    discrepancies: Mapped[list[BoxRequestDiscrepancy]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )
    comments: Mapped[list[BoxRequestComment]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )
    attachments: Mapped[list[BoxRequestAttachment]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )
    operational_exceptions: Mapped[list[BoxRequestException]] = relationship(
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
    lot_id: Mapped[int | None] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    pallet_id: Mapped[int | None] = mapped_column(
        ForeignKey("pallets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Immutable display snapshot captured when the request item is created.
    lot: Mapped[str | None] = mapped_column(String(64))
    pallet: Mapped[str | None] = mapped_column(String(64))
    box_number: Mapped[str | None] = mapped_column(String(64))
    contents: Mapped[str | None] = mapped_column(Text)
    lot_barcode: Mapped[str | None] = mapped_column(String(18))
    pallet_barcode: Mapped[str | None] = mapped_column(String(18))
    box_barcode: Mapped[str | None] = mapped_column(String(18))

    request: Mapped[BoxRequest] = relationship(back_populates="items")
    lot_record: Mapped[Lot | None] = relationship()
    pallet_record: Mapped[Pallet | None] = relationship()
    file_snapshots: Mapped[list[BoxRequestItemFileSnapshot]] = relationship(
        back_populates="request_item",
        cascade="all, delete-orphan",
        order_by="BoxRequestItemFileSnapshot.position",
        lazy="selectin",
    )

    @validates("lot")
    def _keep_lot_snapshot_immutable(
        self,
        _key: str,
        value: str | None,
    ) -> str | None:
        if "lot" in self.__dict__ and self.__dict__["lot"] != value:
            raise ValueError("request item lot snapshot is immutable")
        return value

    @validates("pallet")
    def _keep_pallet_snapshot_immutable(
        self,
        _key: str,
        value: str | None,
    ) -> str | None:
        if "pallet" in self.__dict__ and self.__dict__["pallet"] != value:
            raise ValueError("request item pallet snapshot is immutable")
        return value

    @validates("lot_barcode", "pallet_barcode", "box_barcode")
    def _keep_barcode_snapshot_write_once(
        self,
        key: str,
        value: str | None,
    ) -> str | None:
        previous = self.__dict__.get(key)
        if key in self.__dict__ and previous != value:
            if previous is not None or value is None:
                raise ValueError(f"request item {key} snapshot is write-once")
            from app.services.barcodes import parse_barcode

            kind, _ = parse_barcode(value)
            if kind.value != key.removesuffix("_barcode"):
                raise ValueError(f"request item {key} has the wrong barcode kind")
        return value

    @property
    def pallet_number(self) -> str | None:
        """Explicit API alias for the immutable pallet display snapshot."""
        return self.pallet


class BoxRequestItemFileSnapshot(Base):
    """Immutable file details captured for one request item."""

    __tablename__ = "box_request_item_file_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "request_item_id",
            "position",
            name="uq_request_item_file_snapshots_position",
        ),
        CheckConstraint(
            "length(trim(reference)) > 0",
            name="ck_request_item_file_snapshots_reference_not_blank",
        ),
        CheckConstraint(
            "position > 0",
            name="ck_request_item_file_snapshots_position_positive",
        ),
        Index("ix_request_item_file_snapshots_file_id", "file_id"),
        Index(
            "ix_request_item_file_snapshots_kind",
            "snapshot_kind",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_item_id: Mapped[int] = mapped_column(
        ForeignKey("box_request_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("box_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_kind: Mapped[BoxRequestItemFileSnapshotKind] = mapped_column(
        Enum(
            BoxRequestItemFileSnapshotKind,
            name="box_request_item_file_snapshot_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request_item: Mapped[BoxRequestItem] = relationship(back_populates="file_snapshots")
    file: Mapped[BoxFile | None] = relationship(back_populates="request_snapshots")

    @validates(
        "request_item_id",
        "file_id",
        "reference",
        "description",
        "barcode",
        "position",
        "snapshot_kind",
    )
    def _keep_snapshot_immutable(self, key: str, value: object) -> object:
        # Staged receipts capture immutable values before a live BoxFile exists.
        # Completion may attach that snapshot once; an established link cannot
        # subsequently be redirected.
        if (
            key in {"file_id", "barcode"}
            and self.__dict__.get(key) is None
            and value is not None
        ):
            if key == "file_id" and not isinstance(value, int):
                raise ValueError("request item file_id snapshot must be an integer")
            if key == "barcode":
                from app.services.barcodes import parse_barcode

                kind, _ = parse_barcode(str(value))
                if kind.value != "file":
                    raise ValueError("request item file barcode has the wrong kind")
            return value
        if key in self.__dict__ and self.__dict__[key] != value:
            raise ValueError(f"request item file {key} snapshot is immutable")
        return value


class BoxRequestEvent(Base):
    __tablename__ = "box_request_events"
    __table_args__ = (
        Index("ix_box_request_events_request_occurred", "request_id", "occurred_at"),
        Index("ix_box_request_events_occurred_type", "occurred_at", "event_type"),
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
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default="{}"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BoxRequestException(Base):
    __tablename__ = "box_request_exceptions"
    __table_args__ = (
        Index(
            "ix_box_request_exceptions_request_resolution",
            "request_id",
            "resolved_at",
            "created_at",
        ),
        Index(
            "ix_box_request_exceptions_kind_created",
            "exception_kind",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False
    )
    exception_kind: Mapped[BoxRequestExceptionKind] = mapped_column(
        Enum(
            BoxRequestExceptionKind,
            name="box_request_exception_kind",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    revised_window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    revised_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_target: Mapped[BoxRequestStatus] = mapped_column(
        Enum(BoxRequestStatus, name="box_request_status", create_type=False),
        nullable=False,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(Text)

    request: Mapped[BoxRequest] = relationship(back_populates="operational_exceptions")


class BoxRequestDocument(Base):
    __tablename__ = "box_request_documents"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_box_request_documents_object_key"),
        Index("ix_box_request_documents_request_type", "request_id", "document_type"),
        Index(
            "ix_box_request_documents_request_type_current",
            "request_id",
            "document_type",
            "is_current",
        ),
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


class BoxRequestComment(Base):
    __tablename__ = "box_request_comments"
    __table_args__ = (
        Index("ix_box_request_comments_request_created", "request_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped[BoxRequest] = relationship(back_populates="comments")


class BoxRequestAttachment(Base):
    """Supporting request material that is not an ERP delivery/return note."""

    __tablename__ = "box_request_attachments"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_box_request_attachments_object_key"),
        Index("ix_box_request_attachments_request_created", "request_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped[BoxRequest] = relationship(back_populates="attachments")


class BoxRequestDiscrepancy(Base):
    __tablename__ = "box_request_discrepancies"
    __table_args__ = (
        Index("ix_request_discrepancies_request_type", "request_id", "discrepancy_type"),
        Index("ix_request_discrepancies_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("box_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("box_request_items.id", ondelete="SET NULL"), index=True
    )
    box_id: Mapped[int | None] = mapped_column(
        ForeignKey("boxes.id", ondelete="SET NULL"), index=True
    )
    discrepancy_type: Mapped[BoxRequestDiscrepancyType] = mapped_column(
        Enum(
            BoxRequestDiscrepancyType,
            name="box_request_discrepancy_type",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    quantity: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request: Mapped[BoxRequest] = relationship(back_populates="discrepancies")
    photos: Mapped[list[BoxRequestDiscrepancyPhoto]] = relationship(
        back_populates="discrepancy", cascade="all, delete-orphan", lazy="selectin"
    )


class BoxRequestDiscrepancyPhoto(Base):
    __tablename__ = "box_request_discrepancy_photos"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_request_discrepancy_photos_object_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    discrepancy_id: Mapped[int] = mapped_column(
        ForeignKey("box_request_discrepancies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    discrepancy: Mapped[BoxRequestDiscrepancy] = relationship(back_populates="photos")


__all__ = [
    "ACTIVE_REQUEST_STATUSES",
    "BLOCKING_REQUEST_STATUSES",
    "BoxRequest",
    "BoxRequestAttachment",
    "BoxRequestComment",
    "BoxRequestDirection",
    "BoxRequestDiscrepancy",
    "BoxRequestDiscrepancyPhoto",
    "BoxRequestDiscrepancyType",
    "BoxRequestDocument",
    "BoxRequestDocumentType",
    "BoxRequestEvent",
    "BoxRequestEventType",
    "BoxRequestException",
    "BoxRequestExceptionKind",
    "BoxRequestItem",
    "BoxRequestItemFileSnapshot",
    "BoxRequestItemFileSnapshotKind",
    "BoxRequestPriority",
    "BoxRequestStatus",
]
