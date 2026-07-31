"""record actual inbound delivery count and variance

Revision ID: 0014_delivery_count_variance
Revises: 0013_box_requests
Create Date: 2026-07-31 12:06:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_delivery_count_variance"
down_revision = "0013_box_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "box_requests",
        sa.Column("actual_received_quantity", sa.Integer(), nullable=True),
    )
    op.add_column(
        "box_requests",
        sa.Column("variance_quantity", sa.Integer(), nullable=True),
    )
    op.add_column(
        "box_requests",
        sa.Column("discrepancy_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("box_requests", "discrepancy_reason")
    op.drop_column("box_requests", "variance_quantity")
    op.drop_column("box_requests", "actual_received_quantity")
