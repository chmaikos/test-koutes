from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base

if TYPE_CHECKING:
    from app.models.boxes import Box
    from app.models.lots import Lot


MAX_PALLET_NUMBER_LENGTH = 64


def clean_pallet_number(value: str) -> str:
    """Trim a pallet identifier and collapse whitespace without changing case."""
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("pallet number must not be blank")
    if len(cleaned) > MAX_PALLET_NUMBER_LENGTH:
        raise ValueError(
            f"pallet number must be at most {MAX_PALLET_NUMBER_LENGTH} characters"
        )
    return cleaned


def clean_optional_pallet_number(value: str | None) -> str | None:
    """Canonicalize an optional pallet identifier, treating blanks as omitted."""
    if value is None or not value.strip():
        return None
    return clean_pallet_number(value)


def normalize_pallet_number(value: str) -> str:
    """Return the case-insensitive identity key used within a lot."""
    return clean_pallet_number(value).lower()


class PalletEventType(str, enum.Enum):
    created = "created"
    renumbered = "renumbered"
    archived = "archived"
    restored = "restored"
    moved = "moved"
    lot_reassigned = "lot_reassigned"
    merged_absorbed = "merged_absorbed"
    boxes_assigned = "boxes_assigned"
    boxes_unassigned = "boxes_unassigned"


class Pallet(Base):
    __tablename__ = "pallets"
    __table_args__ = (
        UniqueConstraint(
            "lot_id",
            "normalized_pallet_number",
            name="uq_pallets_lot_normalized_number",
        ),
        CheckConstraint("pallet_number <> ''", name="ck_pallets_number_not_blank"),
        CheckConstraint(
            "normalized_pallet_number <> ''",
            name="ck_pallets_normalized_number_not_blank",
        ),
        CheckConstraint("version > 0", name="ck_pallets_version_positive"),
        CheckConstraint(
            "(is_active AND archived_at IS NULL AND archived_by_user_id IS NULL) "
            "OR (NOT is_active AND archived_at IS NOT NULL)",
            name="ck_pallets_archive_state",
        ),
        CheckConstraint(
            "(absorbed_into_pallet_id IS NULL AND absorbed_at IS NULL "
            "AND absorbed_by_user_id IS NULL) "
            "OR (NOT is_active AND absorbed_into_pallet_id IS NOT NULL "
            "AND absorbed_at IS NOT NULL)",
            name="ck_pallets_absorbed_state",
        ),
        Index("ix_pallets_lot_active", "lot_id", "is_active"),
        Index("ix_pallets_absorbed_into", "absorbed_into_pallet_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False
    )
    pallet_number: Mapped[str] = mapped_column(
        String(MAX_PALLET_NUMBER_LENGTH), nullable=False
    )
    normalized_pallet_number: Mapped[str] = mapped_column(
        String(MAX_PALLET_NUMBER_LENGTH), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archive_reason: Mapped[str | None] = mapped_column(Text)
    absorbed_into_pallet_id: Mapped[int | None] = mapped_column(
        ForeignKey("pallets.id", ondelete="RESTRICT")
    )
    absorbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    absorbed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
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

    lot: Mapped[Lot] = relationship(back_populates="pallets")
    boxes: Mapped[list[Box]] = relationship(back_populates="pallet")
    events: Mapped[list[PalletEvent]] = relationship(
        back_populates="pallet",
        order_by="PalletEvent.occurred_at",
        passive_deletes=True,
    )
    absorbed_into: Mapped[Pallet | None] = relationship(
        remote_side="Pallet.id",
        foreign_keys=[absorbed_into_pallet_id],
    )

    @validates("pallet_number")
    def _normalize_number(self, _key: str, value: str) -> str:
        cleaned = clean_pallet_number(value)
        self.normalized_pallet_number = cleaned.lower()
        return cleaned


class PalletEvent(Base):
    __tablename__ = "pallet_events"
    __table_args__ = (
        Index("ix_pallet_events_pallet_occurred", "pallet_id", "occurred_at"),
        Index("ix_pallet_events_actor_occurred", "actor_user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pallet_id: Mapped[int] = mapped_column(
        ForeignKey("pallets.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[PalletEventType] = mapped_column(
        Enum(
            PalletEventType,
            name="pallet_event_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    old_pallet_number: Mapped[str | None] = mapped_column(
        String(MAX_PALLET_NUMBER_LENGTH)
    )
    new_pallet_number: Mapped[str | None] = mapped_column(
        String(MAX_PALLET_NUMBER_LENGTH)
    )
    from_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    to_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default="{}"
    )

    pallet: Mapped[Pallet] = relationship(back_populates="events")


__all__ = [
    "Pallet",
    "PalletEvent",
    "PalletEventType",
    "clean_pallet_number",
    "normalize_pallet_number",
]
