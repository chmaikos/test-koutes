"""add structured audit metadata and reporting indexes

Revision ID: 0023_request_reporting
Revises: 0022_xlsx_mapping_templates
Create Date: 2026-08-10 21:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_request_reporting"
down_revision = "0022_xlsx_mapping_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    empty_object = sa.text("'{}'::jsonb")
    op.add_column(
        "box_events",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=empty_object,
        ),
    )
    op.add_column(
        "box_request_events",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=empty_object,
        ),
    )
    op.create_index(
        "ix_box_requests_warehouse_submitted",
        "box_requests",
        ["warehouse_id", "submitted_at"],
    )
    op.create_index(
        "ix_box_requests_assignee_status_sla",
        "box_requests",
        ["assigned_mover_user_id", "status", "sla_deadline"],
    )
    op.create_index(
        "ix_box_request_events_occurred_type",
        "box_request_events",
        ["occurred_at", "event_type"],
    )
    op.create_index(
        "ix_box_request_documents_request_type_current",
        "box_request_documents",
        ["request_id", "document_type", "is_current"],
    )
    op.create_index(
        "ix_request_discrepancies_created",
        "box_request_discrepancies",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_discrepancies_created",
        table_name="box_request_discrepancies",
    )
    op.drop_index(
        "ix_box_request_documents_request_type_current",
        table_name="box_request_documents",
    )
    op.drop_index(
        "ix_box_request_events_occurred_type",
        table_name="box_request_events",
    )
    op.drop_index(
        "ix_box_requests_assignee_status_sla", table_name="box_requests"
    )
    op.drop_index(
        "ix_box_requests_warehouse_submitted", table_name="box_requests"
    )
    op.drop_column("box_request_events", "metadata")
    op.drop_column("box_events", "metadata")
