from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BarcodeEntityKind(str, enum.Enum):
    lot = "lot"
    pallet = "pallet"
    box = "box"
    file = "file"


class BarcodeIdentity(Base):
    """Permanent issuance record for one physical warehouse entity."""

    __tablename__ = "barcode_identities"
    __table_args__ = (
        UniqueConstraint("issuance_number", name="uq_barcode_identities_number"),
        UniqueConstraint("barcode", name="uq_barcode_identities_barcode"),
        UniqueConstraint(
            "entity_kind",
            "entity_id",
            name="uq_barcode_identities_kind_entity",
        ),
        CheckConstraint(
            "issuance_number > 0 AND issuance_number <= 999999999999",
            name="ck_barcode_identities_number_range",
        ),
        CheckConstraint(
            "(retired_at IS NULL AND retired_by_user_id IS NULL "
            "AND retirement_reason IS NULL) OR "
            "(retired_at IS NOT NULL AND retirement_reason IS NOT NULL "
            "AND length(trim(retirement_reason)) > 0)",
            name="ck_barcode_identities_retirement_state",
        ),
        Index("ix_barcode_identities_entity", "entity_kind", "entity_id"),
        Index("ix_barcode_identities_retired", "retired_at"),
    )

    # Identity IDs intentionally equal their issuance number. This lets the
    # issuance primitive build both non-null sides of the association before
    # either row is inserted.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    entity_kind: Mapped[BarcodeEntityKind] = mapped_column(
        Enum(
            BarcodeEntityKind,
            name="barcode_entity_kind",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    issuance_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    barcode: Mapped[str] = mapped_column(String(18), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    issued_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    issuance_reason: Mapped[str | None] = mapped_column(Text)
    issuance_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict, server_default="{}"
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    retirement_reason: Mapped[str | None] = mapped_column(Text)
    retirement_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON)


class BarcodeIssuanceCounter(Base):
    """SQLite's transactional substitute for the PostgreSQL sequence."""

    __tablename__ = "barcode_issuance_counter"

    singleton_id: Mapped[int] = mapped_column(primary_key=True)
    next_number: Mapped[int] = mapped_column(BigInteger, nullable=False)


class BarcodeFileMigrationAudit(Base):
    """Permanent admin-only evidence for legacy File barcode replacement."""

    __tablename__ = "barcode_file_migration_audit"

    file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    identity_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    issuance_number: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True
    )
    old_barcode: Mapped[str | None] = mapped_column(String(255))
    new_barcode: Mapped[str] = mapped_column(String(18), nullable=False, unique=True)
    migrated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "BarcodeEntityKind",
    "BarcodeFileMigrationAudit",
    "BarcodeIdentity",
    "BarcodeIssuanceCounter",
]
