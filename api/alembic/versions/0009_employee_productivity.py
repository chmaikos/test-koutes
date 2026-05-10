"""employee productivity tracker: employees, entries, report runs

Revision ID: 0009_employee_productivity
Revises: 0008_alert_type_extensions
Create Date: 2026-05-10 23:30:00.000000

Introduces the per-warehouse employee productivity feature:

* ``employees`` -- admin-managed roster of warehouse staff. Soft-deleted
  via ``is_active = false`` so historical productivity rows stay
  attributable to a stable record.
* ``productivity_entries`` -- one row per (employee, day). Operators
  upsert this table when they record the day's pages + hours; the unique
  constraint on (employee_id, entry_date) is what makes the upsert work.
  ``warehouse_id`` is denormalised from the employee row so the
  aggregation queries powering the dashboard and email don't need a join.
* ``productivity_report_runs`` -- (warehouse_id, report_date) idempotency
  table for the end-of-day email job. A successful row blocks a re-send
  even if APScheduler fires the daily job twice.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_employee_productivity"
down_revision = "0008_alert_type_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column(
            "default_hours_per_day",
            sa.Numeric(4, 2),
            nullable=False,
            server_default=sa.text("8.00"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_employees_warehouse_active",
        "employees",
        ["warehouse_id", "is_active"],
    )

    op.create_table(
        "productivity_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("hours_worked", sa.Numeric(5, 2), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "employee_id", "entry_date", name="uq_productivity_employee_date"
        ),
        sa.CheckConstraint("pages >= 0", name="ck_productivity_pages_nonneg"),
        sa.CheckConstraint(
            "hours_worked > 0", name="ck_productivity_hours_positive"
        ),
    )
    op.create_index(
        "ix_productivity_warehouse_date",
        "productivity_entries",
        ["warehouse_id", "entry_date"],
    )

    op.create_table(
        "productivity_report_runs",
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report_date", sa.Date(), primary_key=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "ok", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "recipients", sa.String(2000), nullable=False, server_default=""
        ),
        sa.Column("error", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("productivity_report_runs")
    op.drop_index(
        "ix_productivity_warehouse_date", table_name="productivity_entries"
    )
    op.drop_table("productivity_entries")
    op.drop_index("ix_employees_warehouse_active", table_name="employees")
    op.drop_table("employees")
