"""Revoke local sessions after credential rotation.

Revision ID: 0037_local_session_version
Revises: 0036_barcode_registry
"""
import sqlalchemy as sa

from alembic import op

revision = "0037_local_session_version"
down_revision = "0036_barcode_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("local_session_version", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "local_session_version")
