"""rename boxes.owner to lot and add boxes.contents

Revision ID: 0006_box_lot_contents
Revises: 0005_user_warehouse_access
Create Date: 2026-05-09 12:00:00.000000

* Renames ``boxes.owner`` (``VARCHAR(200) NOT NULL DEFAULT ''``) to
  ``boxes.lot`` (``VARCHAR(64) NOT NULL``, no default). Required, free-text.
* Adds ``boxes.contents`` (``VARCHAR(200) NULL``) for an optional descriptor.

Backfill rules applied before the type/length change:

* Rows with empty or NULL ``owner`` are stamped with the literal ``'UNKNOWN'``
  so the new ``NOT NULL`` constraint is satisfied without forcing a manual
  cleanup pass.
* Rows whose ``owner`` exceeds 64 characters are truncated to the first 64.

Both transformations are destructive and cannot be reversed by ``downgrade()``;
the column structure is restored but original values are not.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_box_lot_contents"
down_revision = "0005_user_warehouse_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill: ensure every row will satisfy the new NOT NULL + length(64)
    # constraints before we tighten them.
    op.execute(
        "UPDATE boxes SET owner = 'UNKNOWN' WHERE owner IS NULL OR owner = ''"
    )
    op.execute(
        "UPDATE boxes SET owner = LEFT(owner, 64) WHERE LENGTH(owner) > 64"
    )

    # Drop the legacy server_default ('') so the renamed column has no default
    # (the API layer is responsible for supplying lot on every insert).
    op.execute("ALTER TABLE boxes ALTER COLUMN owner DROP DEFAULT")

    op.alter_column(
        "boxes",
        "owner",
        new_column_name="lot",
        existing_type=sa.String(200),
        type_=sa.String(64),
        existing_nullable=False,
    )

    op.add_column(
        "boxes",
        sa.Column("contents", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    # Restore the column structure; truncated/backfilled values cannot be
    # recovered.
    op.drop_column("boxes", "contents")

    op.alter_column(
        "boxes",
        "lot",
        new_column_name="owner",
        existing_type=sa.String(64),
        type_=sa.String(200),
        existing_nullable=False,
    )

    op.execute("ALTER TABLE boxes ALTER COLUMN owner SET DEFAULT ''")
