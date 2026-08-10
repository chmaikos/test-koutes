from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReceiptMode(str, enum.Enum):
    auto_complete = "auto_complete"
    admin_review = "admin_review"


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint(
            "min_pages_per_day IS NULL OR min_pages_per_day > 0",
            name="ck_warehouses_min_pages_per_day_positive",
        ),
        CheckConstraint(
            "lead_time_days >= 0 AND lead_time_days <= 365",
            name="ck_warehouses_lead_time_days_range",
        ),
        CheckConstraint(
            "safety_stock_percent >= 0 AND safety_stock_percent <= 500",
            name="ck_warehouses_safety_stock_percent_range",
        ),
        CheckConstraint(
            "history_30_weight >= 0 AND history_30_weight <= 1000 "
            "AND history_90_weight >= 0 AND history_90_weight <= 1000 "
            "AND history_30_weight + history_90_weight > 0",
            name="ck_warehouses_history_weights_range",
        ),
        CheckConstraint(
            "forecast_adjustment IS NULL OR "
            "(forecast_adjustment >= -5000 AND forecast_adjustment <= 5000)",
            name="ck_warehouses_forecast_adjustment_range",
        ),
        CheckConstraint(
            "two_person_approval_threshold IS NULL OR "
            "two_person_approval_threshold > 0",
            name="ck_warehouses_two_person_threshold_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    min_inventory: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_capacity: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    min_pages_per_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safety_stock_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    history_30_weight: Mapped[int] = mapped_column(
        Integer, nullable=False, default=70
    )
    history_90_weight: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    forecast_adjustment: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    receipt_mode: Mapped[ReceiptMode] = mapped_column(
        Enum(
            ReceiptMode,
            name="warehouse_receipt_mode",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=ReceiptMode.auto_complete,
    )
    require_erp_document: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    quarantine_imports: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    quarantine_manual_receipts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    two_person_approval_threshold: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class WarehousePolicyEvent(Base):
    __tablename__ = "warehouse_policy_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    changed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    old_policy: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    new_policy: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["ReceiptMode", "Warehouse", "WarehousePolicyEvent"]
