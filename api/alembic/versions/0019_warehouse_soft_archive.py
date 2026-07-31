"""add reversible warehouse soft archive

Revision ID: 0019_warehouse_soft_archive
Revises: 0018_productivity_reporting
Create Date: 2026-07-31 17:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_warehouse_soft_archive"
down_revision = "0018_productivity_reporting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "warehouses",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "warehouses",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "warehouses",
        sa.Column(
            "archived_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_warehouses_is_active",
        "warehouses",
        ["is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_warehouses_is_active", table_name="warehouses")
    op.drop_column("warehouses", "archived_by_user_id")
    op.drop_column("warehouses", "archived_at")
    op.drop_column("warehouses", "is_active")
