"""add audited box requests, ERP documents, and warehouse mover role

Revision ID: 0013_box_requests
Revises: 0012_exclude_metrics_drop_email
Create Date: 2026-07-31 10:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_box_requests"
down_revision = "0012_exclude_metrics_drop_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum additions cannot be expressed portably through Alembic.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'warehouse_mover'")

    direction = postgresql.ENUM(
        "inbound", "return", name="box_request_direction", create_type=False
    )
    request_status = postgresql.ENUM(
        "submitted",
        "approved",
        "in_transit",
        "completed",
        "rejected",
        "cancelled",
        name="box_request_status",
        create_type=False,
    )
    event_type = postgresql.ENUM(
        "submitted",
        "approved",
        "rejected",
        "in_transit",
        "completed",
        "cancelled",
        "document_uploaded",
        name="box_request_event_type",
        create_type=False,
    )
    document_type = postgresql.ENUM(
        "delivery_note",
        "return_note",
        "other",
        name="box_request_document_type",
        create_type=False,
    )
    direction.create(op.get_bind(), checkfirst=True)
    request_status.create(op.get_bind(), checkfirst=True)
    event_type.create(op.get_bind(), checkfirst=True)
    document_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "box_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("direction", direction, nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", request_status, nullable=False),
        sa.Column("requester_user_id", sa.Integer(), nullable=False),
        sa.Column("suggestion_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_available", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_inventory", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_inbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_return", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("cancellation_reason", sa.Text()),
        sa.Column("approved_by_user_id", sa.Integer()),
        sa.Column("in_transit_by_user_id", sa.Integer()),
        sa.Column("completed_by_user_id", sa.Integer()),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("in_transit_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["in_transit_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completed_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_box_requests_warehouse_status", "box_requests", ["warehouse_id", "status"]
    )
    op.create_index(
        "ix_box_requests_requester_created",
        "box_requests",
        ["requester_user_id", "created_at"],
    )
    op.create_index("ix_box_requests_warehouse_id", "box_requests", ["warehouse_id"])
    op.create_index("ix_box_requests_requester_user_id", "box_requests", ["requester_user_id"])

    op.create_table(
        "box_request_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("box_id", sa.Integer()),
        sa.Column("lot", sa.String(length=64)),
        sa.Column("box_number", sa.String(length=64)),
        sa.Column("contents", sa.String(length=200)),
        sa.ForeignKeyConstraint(["request_id"], ["box_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["box_id"], ["boxes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("request_id", "position", name="uq_box_request_items_position"),
    )
    op.create_index("ix_box_request_items_request_id", "box_request_items", ["request_id"])
    op.create_index("ix_box_request_items_box_id", "box_request_items", ["box_id"])

    op.create_table(
        "box_request_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("from_status", request_status),
        sa.Column("to_status", request_status),
        sa.Column("user_id", sa.Integer()),
        sa.Column("note", sa.Text()),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["request_id"], ["box_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_box_request_events_request_occurred",
        "box_request_events",
        ["request_id", "occurred_at"],
    )

    op.create_table(
        "box_request_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("document_type", document_type, nullable=False),
        sa.Column("erp_reference", sa.String(length=120), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer()),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["request_id"], ["box_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("object_key", name="uq_box_request_documents_object_key"),
    )
    op.create_index(
        "ix_box_request_documents_request_type",
        "box_request_documents",
        ["request_id", "document_type"],
    )
    op.create_index(
        "ix_box_request_documents_request_id", "box_request_documents", ["request_id"]
    )


def downgrade() -> None:
    op.drop_table("box_request_documents")
    op.drop_table("box_request_events")
    op.drop_table("box_request_items")
    op.drop_table("box_requests")
    for enum_name in (
        "box_request_document_type",
        "box_request_event_type",
        "box_request_status",
        "box_request_direction",
    ):
        postgresql.ENUM(name=enum_name).drop(op.get_bind(), checkfirst=True)
    # PostgreSQL cannot safely remove an enum value while users may still have
    # that value. Leaving warehouse_mover in user_role makes rollback non-lossy.
