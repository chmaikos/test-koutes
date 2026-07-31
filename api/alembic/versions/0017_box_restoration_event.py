"""add audited box restoration event

Revision ID: 0017_box_restoration_event
Revises: 0016_request_box_integrity
Create Date: 2026-07-31 16:05:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0017_box_restoration_event"
down_revision = "0016_request_box_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE box_event_type ADD VALUE IF NOT EXISTS 'restored'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove an enum value while rows may use it.
    pass
