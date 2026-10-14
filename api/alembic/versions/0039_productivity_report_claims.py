"""Durable claims and retry bookkeeping for daily report dispatch.

Revision ID: 0039_productivity_report_claims
Revises: 0038_productivity_entry_audit
"""
import sqlalchemy as sa

from alembic import op

revision = "0039_productivity_report_claims"
down_revision = "0038_productivity_entry_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("productivity_report_runs", column)


def downgrade() -> None:
    for name in ("next_attempt_at", "attempts", "claimed_at", "claim_token"):
        op.drop_column("productivity_report_runs", name)
