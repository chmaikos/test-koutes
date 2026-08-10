from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class XlsxMappingUseCase(str, enum.Enum):
    box_import = "box_import"
    inbound_acceptance = "inbound_acceptance"


class XlsxMappingTemplate(Base):
    __tablename__ = "xlsx_mapping_templates"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "use_case",
            "name",
            name="uq_xlsx_mapping_templates_owner_use_case_name",
        ),
        CheckConstraint("row_start >= 1", name="ck_xlsx_mapping_templates_row_start"),
        CheckConstraint(
            "usage_count >= 0", name="ck_xlsx_mapping_templates_usage_count"
        ),
        Index(
            "ix_xlsx_mapping_templates_match",
            "use_case",
            "header_fingerprint",
            "filename_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=True, index=True
    )
    use_case: Mapped[XlsxMappingUseCase] = mapped_column(
        Enum(XlsxMappingUseCase, name="xlsx_mapping_use_case"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sheet_pattern: Mapped[str] = mapped_column(String(120), nullable=False, default="*")
    filename_fingerprint: Mapped[str] = mapped_column(
        String(255), nullable=False, default=""
    )
    header_fingerprint: Mapped[str] = mapped_column(
        String(2000), nullable=False, default=""
    )
    column_mappings: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
    )
    lot_source: Mapped[str] = mapped_column(String(16), nullable=False)
    fixed_lot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_start: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    include_rows_by_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner = relationship("User")
    warehouse = relationship("Warehouse")
