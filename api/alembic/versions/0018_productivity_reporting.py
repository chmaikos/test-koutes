"""productivity thresholds and positive page entries

Revision ID: 0018_productivity_reporting
Revises: 0017_box_restoration_event
Create Date: 2026-07-31 16:50:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_productivity_reporting"
down_revision = "0017_box_restoration_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warehouses",
        sa.Column("min_pages_per_day", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_warehouses_min_pages_per_day_positive",
        "warehouses",
        "min_pages_per_day IS NULL OR min_pages_per_day > 0",
    )

    # Historical zero-page entries remain readable. PostgreSQL enforces a
    # NOT VALID check for new and updated rows without scanning/rejecting
    # legacy rows that complied with the previous business rule.
    op.drop_constraint(
        "ck_productivity_pages_nonneg",
        "productivity_entries",
        type_="check",
    )
    op.execute(
        """
        ALTER TABLE productivity_entries
        ADD CONSTRAINT ck_productivity_pages_positive
        CHECK (pages > 0) NOT VALID
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_productivity_pages_positive",
        "productivity_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_productivity_pages_nonneg",
        "productivity_entries",
        "pages >= 0",
    )
    op.drop_constraint(
        "ck_warehouses_min_pages_per_day_positive",
        "warehouses",
        type_="check",
    )
    op.drop_column("warehouses", "min_pages_per_day")
