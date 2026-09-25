from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base

if TYPE_CHECKING:
    from app.models.barcode_identities import BarcodeIdentity
    from app.models.boxes import Box
    from app.models.lots import Lot
    from app.models.requests import BoxRequestItemFileSnapshot


MAX_BOX_FILE_REFERENCE_LENGTH = 255


def clean_box_file_reference(value: str) -> str:
    """Trim a file reference and collapse each whitespace run to one space."""
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("file reference must not be blank")
    if len(cleaned) > MAX_BOX_FILE_REFERENCE_LENGTH:
        raise ValueError(
            f"file reference must be at most {MAX_BOX_FILE_REFERENCE_LENGTH} characters"
        )
    return cleaned


def normalize_box_file_reference(value: str) -> str:
    """Return the case/whitespace-insensitive identity key within a lot."""
    return clean_box_file_reference(value).lower()


class BoxFileEventType(str, enum.Enum):
    created = "created"
    updated = "updated"
    moved = "moved"
    archived = "archived"
    restored = "restored"
    box_moved = "box_moved"
    box_status_changed = "box_status_changed"
    lot_reassigned = "lot_reassigned"


class BoxFile(Base):
    """A physical tracked file held inside a box."""

    __tablename__ = "box_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["box_id", "lot_id"],
            ["boxes.id", "boxes.lot_id"],
            name="fk_box_files_box_lot_boxes",
            ondelete="RESTRICT",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint(
            "lot_id",
            "normalized_reference",
            name="uq_box_files_lot_normalized_reference",
        ),
        UniqueConstraint(
            "box_id",
            "position",
            name="uq_box_files_box_position",
        ),
        CheckConstraint(
            "length(trim(reference)) > 0",
            name="ck_box_files_reference_not_blank",
        ),
        CheckConstraint(
            "length(trim(normalized_reference)) > 0",
            name="ck_box_files_normalized_reference_not_blank",
        ),
        CheckConstraint("position > 0", name="ck_box_files_position_positive"),
        CheckConstraint("version > 0", name="ck_box_files_version_positive"),
        CheckConstraint(
            "archive_reason IS NULL OR archived_at IS NOT NULL",
            name="ck_box_files_archive_reason_state",
        ),
        Index("ix_box_files_lot_archived", "lot_id", "archived_at"),
        Index("ix_box_files_box_archived", "box_id", "archived_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    barcode_identity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("barcode_identities.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False)
    box_id: Mapped[int] = mapped_column(nullable=False)
    reference: Mapped[str] = mapped_column(String(MAX_BOX_FILE_REFERENCE_LENGTH), nullable=False)
    normalized_reference: Mapped[str] = mapped_column(
        String(MAX_BOX_FILE_REFERENCE_LENGTH), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __mapper_args__ = {"version_id_col": version}

    lot: Mapped[Lot] = relationship(back_populates="files", overlaps="box,files")
    barcode_identity: Mapped[BarcodeIdentity] = relationship(
        "BarcodeIdentity",
        foreign_keys=[barcode_identity_id],
        lazy="joined",
        innerjoin=True,
    )
    box: Mapped[Box] = relationship(back_populates="files", overlaps="files,lot")
    events: Mapped[list[BoxFileEvent]] = relationship(
        back_populates="file",
        order_by="BoxFileEvent.occurred_at",
        passive_deletes=True,
    )
    request_snapshots: Mapped[list[BoxRequestItemFileSnapshot]] = relationship(
        back_populates="file",
        passive_deletes=True,
    )

    @validates("reference")
    def _normalize_reference(self, _key: str, value: str) -> str:
        cleaned = clean_box_file_reference(value)
        self.normalized_reference = cleaned.lower()
        return cleaned

    @hybrid_property
    def barcode(self) -> str:
        return self.barcode_identity.barcode

    @barcode.inplace.expression
    @classmethod
    def _barcode_expression(cls):
        from app.models.barcode_identities import BarcodeIdentity

        return (
            select(BarcodeIdentity.barcode)
            .where(BarcodeIdentity.id == cls.barcode_identity_id)
            .scalar_subquery()
        )


class BoxFileEvent(Base):
    """Append-only audit event with self-contained before/after snapshots."""

    __tablename__ = "box_file_events"
    __table_args__ = (
        CheckConstraint(
            "before_snapshot IS NOT NULL OR after_snapshot IS NOT NULL",
            name="ck_box_file_events_has_snapshot",
        ),
        Index("ix_box_file_events_file_occurred", "file_id", "occurred_at"),
        Index("ix_box_file_events_actor_occurred", "actor_user_id", "occurred_at"),
        Index("ix_box_file_events_type_occurred", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("box_files.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[BoxFileEventType] = mapped_column(
        Enum(
            BoxFileEventType,
            name="box_file_event_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    before_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default="{}"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    file: Mapped[BoxFile] = relationship(back_populates="events")


__all__ = [
    "BoxFile",
    "BoxFileEvent",
    "BoxFileEventType",
    "clean_box_file_reference",
    "normalize_box_file_reference",
]
