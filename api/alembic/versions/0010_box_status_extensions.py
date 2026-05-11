"""extend box_status enum with processing and incomplete

Revision ID: 0010_box_status_extensions
Revises: 0009_employee_productivity
Create Date: 2026-05-11 17:00:00.000000

Adds two new states to the ``box_status`` Postgres enum so we can track
the open-box lifecycle in addition to the closed-only states the app
shipped with:

* ``processing``     -- box has been opened, still has stuff to process
* ``incomplete``     -- box has been emptied, but pages produced from it
                        are still being processed elsewhere

Both values slot in *before* ``ready_to_return`` so a hypothetical
``ORDER BY status`` lays the chain out in the natural sequence
``received -> processing -> incomplete -> ready_to_return -> returned``.

PG10+ supports ``ALTER TYPE ... ADD VALUE`` which is an atomic, lock-free
change; no existing rows need rewriting and the migration is safe to run
while the API is live. The downgrade rebuilds the type the same way the
``0003_shrink_box_status`` migration does because Postgres cannot drop a
value from an existing enum.
"""
from __future__ import annotations

from alembic import op

revision = "0010_box_status_extensions"
down_revision = "0009_employee_productivity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction in some PG
    # versions; alembic opens one by default. COMMIT here first so the
    # ADD VALUE statements run in their own implicit transaction. The
    # later migration ops re-open the transaction automatically.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE box_status ADD VALUE IF NOT EXISTS 'processing' "
        "BEFORE 'ready_to_return'"
    )
    op.execute(
        "ALTER TYPE box_status ADD VALUE IF NOT EXISTS 'incomplete' "
        "BEFORE 'ready_to_return'"
    )


def downgrade() -> None:
    # Postgres cannot drop values from an existing enum, so we recreate
    # the type with the original three-value shape, casting columns over.
    # Any rows currently holding ``processing`` or ``incomplete`` are
    # remapped to a neighbour first so the cast does not fail; this is a
    # lossy step on purpose -- a clean rollback after operators have used
    # the new states would otherwise be impossible.
    op.execute(
        "UPDATE boxes SET status = 'received' WHERE status = 'processing'"
    )
    op.execute(
        "UPDATE boxes SET status = 'ready_to_return' "
        "WHERE status = 'incomplete'"
    )
    op.execute(
        "UPDATE box_events SET from_status = 'received' "
        "WHERE from_status = 'processing'"
    )
    op.execute(
        "UPDATE box_events SET from_status = 'ready_to_return' "
        "WHERE from_status = 'incomplete'"
    )
    op.execute(
        "UPDATE box_events SET to_status = 'received' "
        "WHERE to_status = 'processing'"
    )
    op.execute(
        "UPDATE box_events SET to_status = 'ready_to_return' "
        "WHERE to_status = 'incomplete'"
    )

    op.execute("ALTER TYPE box_status RENAME TO box_status_old")
    op.execute(
        "CREATE TYPE box_status AS ENUM ("
        "'received', 'ready_to_return', 'returned')"
    )
    op.execute("ALTER TABLE boxes ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE boxes ALTER COLUMN status "
        "TYPE box_status USING status::text::box_status"
    )
    op.execute(
        "ALTER TABLE boxes ALTER COLUMN status "
        "SET DEFAULT 'received'::box_status"
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
