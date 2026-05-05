"""shrink box_status enum to three values

Revision ID: 0003_shrink_box_status
Revises: 0002_local_auth
Create Date: 2026-05-05 21:00:00.000000

Drops ``in_progress`` and ``processing_complete`` from the ``box_status``
Postgres enum. Existing rows are remapped:

* ``in_progress`` -> ``received``
* ``processing_complete`` -> ``ready_to_return``

The ``boxes.processing_completed_at`` column is intentionally left in place
so historical data is preserved; new mutations no longer write it.
"""
from __future__ import annotations

from alembic import op

revision = "0003_shrink_box_status"
down_revision = "0002_local_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Remap rows that hold soon-to-be-removed enum values. Doing this
    # before the enum narrowing avoids a USING-cast failure.
    op.execute(
        "UPDATE boxes SET status = 'received' "
        "WHERE status = 'in_progress'"
    )
    op.execute(
        "UPDATE boxes SET status = 'ready_to_return' "
        "WHERE status = 'processing_complete'"
    )
    op.execute(
        "UPDATE box_events SET from_status = 'received' "
        "WHERE from_status = 'in_progress'"
    )
    op.execute(
        "UPDATE box_events SET from_status = 'ready_to_return' "
        "WHERE from_status = 'processing_complete'"
    )
    op.execute(
        "UPDATE box_events SET to_status = 'received' "
        "WHERE to_status = 'in_progress'"
    )
    op.execute(
        "UPDATE box_events SET to_status = 'ready_to_return' "
        "WHERE to_status = 'processing_complete'"
    )

    # 2. Swap the enum type. Postgres can't drop values from an existing enum,
    # so we rename the old type, create a new one with the surviving values,
    # cast each column over, and drop the old type.
    op.execute("ALTER TYPE box_status RENAME TO box_status_old")
    op.execute(
        "CREATE TYPE box_status AS ENUM ("
        "'received', 'ready_to_return', 'returned')"
    )
    op.execute(
        "ALTER TABLE boxes ALTER COLUMN status "
        "TYPE box_status USING status::text::box_status"
    )
    op.execute(
        "ALTER TABLE box_events ALTER COLUMN from_status "
        "TYPE box_status USING from_status::text::box_status"
    )
    op.execute(
        "ALTER TABLE box_events ALTER COLUMN to_status "
        "TYPE box_status USING to_status::text::box_status"
    )
    op.execute("DROP TYPE box_status_old")


def downgrade() -> None:
    # Recreate the original 5-value enum shape. The pre-upgrade granularity
    # cannot be reconstructed -- previously remapped rows stay as
    # ``received`` / ``ready_to_return``.
    op.execute("ALTER TYPE box_status RENAME TO box_status_old")
    op.execute(
        "CREATE TYPE box_status AS ENUM ("
        "'received', 'in_progress', 'processing_complete', "
        "'ready_to_return', 'returned')"
    )
    op.execute(
        "ALTER TABLE boxes ALTER COLUMN status "
        "TYPE box_status USING status::text::box_status"
    )
    op.execute(
        "ALTER TABLE box_events ALTER COLUMN from_status "
        "TYPE box_status USING from_status::text::box_status"
    )
    op.execute(
        "ALTER TABLE box_events ALTER COLUMN to_status "
        "TYPE box_status USING to_status::text::box_status"
    )
    op.execute("DROP TYPE box_status_old")
