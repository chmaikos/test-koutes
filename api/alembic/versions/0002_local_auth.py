"""local (break-glass) admin auth

Revision ID: 0002_local_auth
Revises: 0001_initial
Create Date: 2026-05-04 09:00:00.000000

Adds the columns required for a local username/password "break-glass" admin
that works even when M365 SSO is unavailable. The default user itself is
provisioned at API startup, not in the migration, so admins can change the
defaults via env vars without a schema migration.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_local_auth"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows are SSO users, so entra_oid is populated; we only relax the
    # NOT NULL so future local users can omit it.
    op.alter_column("users", "entra_oid", existing_type=sa.String(64), nullable=True)

    op.add_column(
        "users", sa.Column("username", sa.String(64), nullable=True)
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.add_column(
        "users", sa.Column("password_hash", sa.String(255), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "is_local",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "must_change_credentials",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_credentials")
    op.drop_column("users", "is_local")
    op.drop_column("users", "password_hash")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
    op.alter_column("users", "entra_oid", existing_type=sa.String(64), nullable=False)
