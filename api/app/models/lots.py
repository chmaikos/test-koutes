from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
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


MAX_LOT_NAME_LENGTH = 64


def clean_lot_name(value: str) -> str:
    """Trim a display name and collapse each whitespace run to one space."""
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("lot name must not be blank")
    if len(cleaned) > MAX_LOT_NAME_LENGTH:
        raise ValueError(f"lot name must be at most {MAX_LOT_NAME_LENGTH} characters")
    return cleaned


def normalize_lot_name(value: str) -> str:
    """Return the global, case-insensitive identity key for a lot name."""
    return clean_lot_name(value).lower()


class LotEventType(str, enum.Enum):
    created = "created"
    renamed = "renamed"
    reassigned = "reassigned"


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_lots_normalized_name"),
        CheckConstraint("name <> ''", name="ck_lots_name_not_blank"),
        CheckConstraint("normalized_name <> ''", name="ck_lots_normalized_name_not_blank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_LOT_NAME_LENGTH), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(MAX_LOT_NAME_LENGTH), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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

    boxes: Mapped[list[Box]] = relationship(back_populates="lot_record")
    events: Mapped[list[LotEvent]] = relationship(
        back_populates="lot",
        order_by="LotEvent.occurred_at",
        passive_deletes=True,
    )

    @validates("name")
    def _normalize_name(self, _key: str, value: str) -> str:
        cleaned = clean_lot_name(value)
        self.normalized_name = cleaned.lower()
        return cleaned


class LotEvent(Base):
    __tablename__ = "lot_events"
    __table_args__ = (
        Index("ix_lot_events_lot_occurred", "lot_id", "occurred_at"),
        Index("ix_lot_events_actor_occurred", "actor_user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[LotEventType] = mapped_column(
        Enum(
            LotEventType,
            name="lot_event_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    old_name: Mapped[str | None] = mapped_column(String(MAX_LOT_NAME_LENGTH))
    new_name: Mapped[str | None] = mapped_column(String(MAX_LOT_NAME_LENGTH))
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

    lot: Mapped[Lot] = relationship(back_populates="events")


__all__ = [
    "Lot",
    "LotEvent",
    "LotEventType",
    "clean_lot_name",
    "normalize_lot_name",
]
