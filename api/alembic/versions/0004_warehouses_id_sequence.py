"""attach autoincrementing sequence to warehouses.id

Revision ID: 0004_warehouses_id_sequence
Revises: 0003_shrink_box_status
Create Date: 2026-05-05 21:30:00.000000

The original schema declared ``warehouses.id`` with ``autoincrement=False``
and seeded three rows with explicit ids. Now that admins can create
warehouses from the UI, we attach a Postgres sequence so new rows get an
auto-allocated id without colliding with the seeded ones.
"""
from __future__ import annotations

from alembic import op

revision = "0004_warehouses_id_sequence"
down_revision = "0003_shrink_box_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS warehouses_id_seq")
    op.execute("ALTER SEQUENCE warehouses_id_seq OWNED BY warehouses.id")
    # Advance the sequence past existing rows so the next nextval() doesn't
    # collide with the seeded ids 1/2/3 (or whatever's been added since).
    # setval(..., n) makes the next nextval() return n + 1; using
    # COALESCE(MAX(id), 0) means an empty table still ends up at next=1.
    op.execute(
        "SELECT setval('warehouses_id_seq', "
        "COALESCE((SELECT MAX(id) FROM warehouses), 0))"
    )
    op.execute(
        "ALTER TABLE warehouses ALTER COLUMN id "
        "SET DEFAULT nextval('warehouses_id_seq')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE warehouses ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS warehouses_id_seq")
