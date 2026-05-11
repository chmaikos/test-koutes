from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Employee(Base):
    """A warehouse employee whose daily output operators record.

    Employees are deliberately decoupled from the ``users`` table: most
    pickers/scanners never sign in, and we still want their productivity
    attributable to a stable record. ``is_active`` is a soft-delete flag --
    historical ``productivity_entries`` keep referring to deactivated
    employees so weekly/monthly totals stay correct.
    """

    __tablename__ = "employees"
    __table_args__ = (
        Index("ix_employees_warehouse_active", "warehouse_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Admin-set "working hours" for this employee; pre-fills the daily entry
    # form. Stored as NUMERIC(4,2) to allow fractional shifts like 7.5.
    default_hours_per_day: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=Decimal("8.00")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Admin override: when true the productivity service ignores every
    # entry belonging to this employee. Distinct from ``is_active`` --
    # the employee stays on the roster, just out of the math.
    excluded_from_metrics: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
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


class ProductivityEntry(Base):
    """A single (employee, day) record of pages produced and hours worked.

    ``warehouse_id`` is denormalised from ``employees.warehouse_id`` so the
    aggregate queries powering the dashboard and the daily report don't
    need a join. The unique constraint on ``(employee_id, entry_date)``
    makes the operator-facing endpoint an upsert: re-submitting a day
    overwrites the previous numbers without creating a duplicate.
    """

    __tablename__ = "productivity_entries"
    __table_args__ = (
        UniqueConstraint(
            "employee_id", "entry_date", name="uq_productivity_employee_date"
        ),
        Index(
            "ix_productivity_warehouse_date", "warehouse_id", "entry_date"
        ),
        CheckConstraint("pages >= 0", name="ck_productivity_pages_nonneg"),
        CheckConstraint(
            "hours_worked > 0", name="ck_productivity_hours_positive"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    hours_worked: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Per-entry opt-out for one-off cases (training runs, equipment
    # outages, partial shifts) where the row should exist for audit
    # purposes but not contribute to averages or leaderboards.
    excluded_from_metrics: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class ProductivityReportRun(Base):
    """One row per warehouse, per report date.

    Acts as the idempotency key for the end-of-day email job: the
    dispatcher upserts a row before sending and refuses to re-send if a
    successful row already exists. The (warehouse_id, report_date)
    composite primary key is what makes that ``ON CONFLICT DO NOTHING``
    pattern cheap.
    """

    __tablename__ = "productivity_report_runs"

    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    report_date: Mapped[date] = mapped_column(Date, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recipients: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


__all__ = ["Employee", "ProductivityEntry", "ProductivityReportRun"]
