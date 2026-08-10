"""add configurable self-receipt governance

Revision ID: 0025_receipt_governance
Revises: 0024_demand_recommendations
Create Date: 2026-08-10 22:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0025_receipt_governance"
down_revision = "0024_demand_recommendations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    receipt_mode = postgresql.ENUM(
        "auto_complete", "admin_review", name="warehouse_receipt_mode"
    )
    if bind.dialect.name == "postgresql":
        receipt_mode.create(bind, checkfirst=True)

    op.add_column(
        "warehouses",
        sa.Column(
            "receipt_mode",
            receipt_mode if bind.dialect.name == "postgresql" else sa.String(32),
            nullable=False,
            server_default="auto_complete",
        ),
    )
    op.add_column(
        "warehouses",
        sa.Column("require_erp_document", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "warehouses",
        sa.Column("quarantine_imports", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "warehouses",
        sa.Column(
            "quarantine_manual_receipts",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "warehouses", sa.Column("two_person_approval_threshold", sa.Integer())
    )
    op.create_check_constraint(
        "ck_warehouses_two_person_threshold_positive",
        "warehouses",
        "two_person_approval_threshold IS NULL OR two_person_approval_threshold > 0",
    )
    op.create_table(
        "warehouse_policy_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "changed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "old_policy",
            postgresql.JSONB(astext_type=sa.Text())
            if bind.dialect.name == "postgresql"
            else sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "new_policy",
            postgresql.JSONB(astext_type=sa.Text())
            if bind.dialect.name == "postgresql"
            else sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_warehouse_policy_events_warehouse_id",
        "warehouse_policy_events",
        ["warehouse_id"],
    )
    op.add_column(
        "box_requests",
        sa.Column(
            "receipt_restore_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "box_requests",
        sa.Column(
            "receipt_quarantine",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "box_requests",
        sa.Column(
            "receipt_document_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # PostgreSQL requires enum values to be committed before ORM writes can use
    # them. ALTER TYPE is deliberately outside a transaction for upgrade safety.
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE box_status ADD VALUE IF NOT EXISTS 'quarantined'")


def downgrade() -> None:
    op.drop_column("box_requests", "receipt_document_required")
    op.drop_column("box_requests", "receipt_quarantine")
    op.drop_column("box_requests", "receipt_restore_archived")
    op.drop_index(
        "ix_warehouse_policy_events_warehouse_id",
        table_name="warehouse_policy_events",
    )
    op.drop_table("warehouse_policy_events")
    op.drop_constraint(
        "ck_warehouses_two_person_threshold_positive",
        "warehouses",
        type_="check",
    )
    op.drop_column("warehouses", "two_person_approval_threshold")
    op.drop_column("warehouses", "quarantine_manual_receipts")
    op.drop_column("warehouses", "quarantine_imports")
    op.drop_column("warehouses", "require_erp_document")
    op.drop_column("warehouses", "receipt_mode")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(name="warehouse_receipt_mode").drop(bind, checkfirst=True)
    # PostgreSQL enum values are intentionally retained on downgrade: removing
    # a value is unsafe while historical rows or audit records may reference it.
