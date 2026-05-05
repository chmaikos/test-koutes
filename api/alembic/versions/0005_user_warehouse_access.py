"""per-warehouse ACL: user_warehouse_access join table

Revision ID: 0005_user_warehouse_access
Revises: 0004_warehouses_id_sequence
Create Date: 2026-05-06 00:00:00.000000

Introduces a per-user, per-warehouse ACL. Admins ignore the table entirely
(they always have access to everything). Operators and viewers can only
read/write boxes, see alerts, and receive SSE updates for warehouses they
have explicit rows for.

Backfill: existing non-admin users keep the access they have today by
inserting one row per (user, warehouse). New users created after this
migration default to zero rows -- i.e. no access -- as required.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_user_warehouse_access"
down_revision = "0004_warehouses_id_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_warehouse_access",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_user_warehouse_access_user_id",
        "user_warehouse_access",
        ["user_id"],
    )

    # Non-disruptive rollout: every existing operator/viewer gets a row for
    # every existing warehouse, so they keep the access they have today.
    # Admins are skipped because they bypass the ACL anyway.
    op.execute(
        "INSERT INTO user_warehouse_access (user_id, warehouse_id) "
        "SELECT u.id, w.id FROM users u CROSS JOIN warehouses w "
        "WHERE u.role <> 'admin'"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_warehouse_access_user_id",
        table_name="user_warehouse_access",
    )
    op.drop_table("user_warehouse_access")
