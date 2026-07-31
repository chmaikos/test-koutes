"""link return requests to their source inbound request

Revision ID: 0015_inbound_linked_returns
Revises: 0014_delivery_count_variance
Create Date: 2026-07-31 12:40:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_inbound_linked_returns"
down_revision = "0014_delivery_count_variance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "box_requests",
        sa.Column("source_inbound_request_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_box_requests_source_inbound_request_id",
        "box_requests",
        "box_requests",
        ["source_inbound_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_box_requests_source_inbound_request_id",
        "box_requests",
        ["source_inbound_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_box_requests_source_inbound_request_id",
        table_name="box_requests",
    )
    op.drop_constraint(
        "fk_box_requests_source_inbound_request_id",
        "box_requests",
        type_="foreignkey",
    )
    op.drop_column("box_requests", "source_inbound_request_id")
