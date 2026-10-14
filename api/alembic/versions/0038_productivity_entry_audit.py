"""Retain productivity entry correction history.

Revision ID: 0038_productivity_entry_audit
Revises: 0037_local_session_version
"""

import sqlalchemy as sa

from alembic import op

revision = "0038_productivity_entry_audit"
down_revision = "0037_local_session_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "productivity_entry_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("old_values", sa.JSON()),
        sa.Column("new_values", sa.JSON()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_productivity_entry_events_entry", "productivity_entry_events", ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_productivity_entry_events_entry", table_name="productivity_entry_events")
    op.drop_table("productivity_entry_events")
