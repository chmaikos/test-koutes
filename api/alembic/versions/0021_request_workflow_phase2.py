"""add request coordination and notifications

Revision ID: 0021_request_workflow_phase2
Revises: 0020_request_workflow_phase1
Create Date: 2026-08-10 21:15:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_request_workflow_phase2"
down_revision = "0020_request_workflow_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    priority = postgresql.ENUM(
        "low", "normal", "high", "urgent", name="box_request_priority", create_type=False
    )
    priority.create(op.get_bind(), checkfirst=True)
    for value in (
        "assignment_changed",
        "schedule_changed",
        "coordination_changed",
        "comment_added",
        "attachment_uploaded",
        "overdue",
    ):
        op.execute(
            f"ALTER TYPE box_request_event_type ADD VALUE IF NOT EXISTS '{value}'"
        )

    op.add_column(
        "users",
        sa.Column(
            "email_requests_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "box_requests",
        sa.Column(
            "priority",
            priority,
            nullable=False,
            server_default="normal",
        ),
    )
    op.add_column("box_requests", sa.Column("requested_date", sa.Date(), nullable=True))
    op.add_column(
        "box_requests",
        sa.Column("scheduled_window_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "box_requests",
        sa.Column("scheduled_window_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "box_requests",
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "box_requests",
        sa.Column("assigned_mover_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "box_requests", sa.Column("destination_contact", sa.String(320), nullable=True)
    )
    op.add_column(
        "box_requests", sa.Column("internal_location", sa.String(320), nullable=True)
    )
    op.add_column(
        "box_requests",
        sa.Column("special_handling_instructions", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_box_requests_assigned_mover_user_id",
        "box_requests",
        "users",
        ["assigned_mover_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_box_requests_assigned_mover_user_id",
        "box_requests",
        ["assigned_mover_user_id"],
    )
    op.create_index(
        "ix_box_requests_sla_deadline", "box_requests", ["sla_deadline"]
    )
    op.create_check_constraint(
        "ck_box_requests_scheduled_window",
        "box_requests",
        "scheduled_window_end IS NULL OR scheduled_window_start IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_box_requests_scheduled_window_order",
        "box_requests",
        "scheduled_window_end IS NULL OR scheduled_window_end > scheduled_window_start",
    )

    op.create_table(
        "box_request_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_box_request_comments_request_created",
        "box_request_comments",
        ["request_id", "created_at"],
    )

    op.create_table(
        "box_request_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("object_key", sa.String(512), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
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
        "ix_box_request_attachments_request_created",
        "box_request_attachments",
        ["request_id", "created_at"],
    )

    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("deep_link", sa.String(512), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False, unique=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_in_app_notifications_request_id",
        "in_app_notifications",
        ["request_id"],
    )
    op.create_index(
        "ix_in_app_notifications_user_created",
        "in_app_notifications",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_in_app_notifications_user_read",
        "in_app_notifications",
        ["user_id", "read_at"],
    )

    op.create_table(
        "request_email_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False, unique=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_request_email_outbox_pending",
        "request_email_outbox",
        ["sent_at", "available_at"],
    )
    op.create_index(
        "ix_request_email_outbox_request",
        "request_email_outbox",
        ["request_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("request_email_outbox")
    op.drop_table("in_app_notifications")
    op.drop_table("box_request_attachments")
    op.drop_table("box_request_comments")
    op.drop_constraint(
        "ck_box_requests_scheduled_window_order", "box_requests", type_="check"
    )
    op.drop_constraint(
        "ck_box_requests_scheduled_window", "box_requests", type_="check"
    )
    op.drop_index("ix_box_requests_sla_deadline", table_name="box_requests")
    op.drop_index(
        "ix_box_requests_assigned_mover_user_id", table_name="box_requests"
    )
    op.drop_constraint(
        "fk_box_requests_assigned_mover_user_id",
        "box_requests",
        type_="foreignkey",
    )
    for column in (
        "special_handling_instructions",
        "internal_location",
        "destination_contact",
        "assigned_mover_user_id",
        "sla_deadline",
        "scheduled_window_end",
        "scheduled_window_start",
        "requested_date",
        "priority",
    ):
        op.drop_column("box_requests", column)
    op.drop_column("users", "email_requests_enabled")
    postgresql.ENUM(name="box_request_priority").drop(
        op.get_bind(), checkfirst=True
    )
