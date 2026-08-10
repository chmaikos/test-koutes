"""add request fulfillment lineage and discrepancies

Revision ID: 0020_request_workflow_phase1
Revises: 0019_warehouse_soft_archive
Create Date: 2026-08-10 20:55:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020_request_workflow_phase1"
down_revision = "0019_warehouse_soft_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE box_request_status ADD VALUE IF NOT EXISTS 'draft' BEFORE 'submitted'")
    op.execute("ALTER TYPE box_request_origin ADD VALUE IF NOT EXISTS 'backorder'")
    op.execute("ALTER TYPE box_request_origin ADD VALUE IF NOT EXISTS 'return_reselection'")
    op.execute("ALTER TYPE box_request_event_type ADD VALUE IF NOT EXISTS 'draft_created'")
    op.execute("ALTER TYPE box_request_event_type ADD VALUE IF NOT EXISTS 'partial_completion'")
    op.execute(
        "ALTER TYPE box_request_event_type ADD VALUE IF NOT EXISTS "
        "'discrepancy_photo_uploaded'"
    )

    op.add_column("box_requests", sa.Column("parent_request_id", sa.Integer(), nullable=True))
    op.add_column("box_requests", sa.Column("root_request_id", sa.Integer(), nullable=True))
    op.add_column(
        "box_requests",
        sa.Column("completion_idempotency_key", sa.String(length=120), nullable=True),
    )
    op.create_foreign_key(
        "fk_box_requests_parent_request_id",
        "box_requests",
        "box_requests",
        ["parent_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_box_requests_root_request_id",
        "box_requests",
        "box_requests",
        ["root_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_box_requests_parent_request_id", "box_requests", ["parent_request_id"]
    )
    op.create_index("ix_box_requests_root_request_id", "box_requests", ["root_request_id"])
    op.execute("UPDATE box_requests SET root_request_id = id")

    discrepancy_type = postgresql.ENUM(
        "missing",
        "unexpected",
        "damaged",
        "wrong_lot",
        "wrong_contents",
        "rejected",
        name="box_request_discrepancy_type",
        create_type=False,
    )
    discrepancy_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "box_request_discrepancies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "request_item_id",
            sa.Integer(),
            sa.ForeignKey("box_request_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "box_id",
            sa.Integer(),
            sa.ForeignKey("boxes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("discrepancy_type", discrepancy_type, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="ck_request_discrepancies_positive_quantity",
        ),
    )
    op.create_index(
        "ix_box_request_discrepancies_request_id",
        "box_request_discrepancies",
        ["request_id"],
    )
    op.create_index(
        "ix_box_request_discrepancies_request_item_id",
        "box_request_discrepancies",
        ["request_item_id"],
    )
    op.create_index(
        "ix_box_request_discrepancies_box_id",
        "box_request_discrepancies",
        ["box_id"],
    )
    op.create_index(
        "ix_request_discrepancies_request_type",
        "box_request_discrepancies",
        ["request_id", "discrepancy_type"],
    )

    op.create_table(
        "box_request_discrepancy_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "discrepancy_id",
            sa.Integer(),
            sa.ForeignKey("box_request_discrepancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(length=512), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "uploaded_by_user_id",
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
    )
    op.create_index(
        "ix_box_request_discrepancy_photos_discrepancy_id",
        "box_request_discrepancy_photos",
        ["discrepancy_id"],
    )


def downgrade() -> None:
    op.drop_table("box_request_discrepancy_photos")
    op.drop_table("box_request_discrepancies")
    postgresql.ENUM(name="box_request_discrepancy_type").drop(
        op.get_bind(), checkfirst=True
    )
    op.drop_index("ix_box_requests_root_request_id", table_name="box_requests")
    op.drop_index("ix_box_requests_parent_request_id", table_name="box_requests")
    op.drop_constraint(
        "fk_box_requests_root_request_id", "box_requests", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_box_requests_parent_request_id", "box_requests", type_="foreignkey"
    )
    op.drop_column("box_requests", "completion_idempotency_key")
    op.drop_column("box_requests", "root_request_id")
    op.drop_column("box_requests", "parent_request_id")
