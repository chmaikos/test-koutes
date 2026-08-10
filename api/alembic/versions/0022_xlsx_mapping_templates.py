"""add saved XLSX mapping templates

Revision ID: 0022_xlsx_mapping_templates
Revises: 0021_request_workflow_phase2
Create Date: 2026-08-10 21:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_xlsx_mapping_templates"
down_revision = "0021_request_workflow_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    use_case = postgresql.ENUM(
        "box_import",
        "inbound_acceptance",
        name="xlsx_mapping_use_case",
        create_type=False,
    )
    use_case.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "xlsx_mapping_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("use_case", use_case, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "sheet_pattern", sa.String(120), nullable=False, server_default="*"
        ),
        sa.Column(
            "filename_fingerprint", sa.String(255), nullable=False, server_default=""
        ),
        sa.Column(
            "header_fingerprint", sa.String(2000), nullable=False, server_default=""
        ),
        sa.Column("column_mappings", postgresql.JSONB(), nullable=False),
        sa.Column("lot_source", sa.String(16), nullable=False),
        sa.Column("fixed_lot", sa.String(64), nullable=True),
        sa.Column("row_start", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "include_rows_by_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "row_start >= 1", name="ck_xlsx_mapping_templates_row_start"
        ),
        sa.CheckConstraint(
            "usage_count >= 0", name="ck_xlsx_mapping_templates_usage_count"
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "use_case",
            "name",
            name="uq_xlsx_mapping_templates_owner_use_case_name",
        ),
    )
    op.create_index(
        "ix_xlsx_mapping_templates_owner_user_id",
        "xlsx_mapping_templates",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_xlsx_mapping_templates_warehouse_id",
        "xlsx_mapping_templates",
        ["warehouse_id"],
    )
    op.create_index(
        "ix_xlsx_mapping_templates_match",
        "xlsx_mapping_templates",
        ["use_case", "header_fingerprint", "filename_fingerprint"],
    )


def downgrade() -> None:
    op.drop_table("xlsx_mapping_templates")
    postgresql.ENUM(name="xlsx_mapping_use_case").drop(
        op.get_bind(), checkfirst=True
    )
