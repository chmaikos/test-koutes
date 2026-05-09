"""extend alert_type enum with leading-indicator + box_stuck values

Revision ID: 0008_alert_type_extensions
Revises: 0007_alert_lifecycle
Create Date: 2026-05-09 15:00:00.000000

Adds three new ``alert_type`` enum values:

* ``near_capacity`` -- inventory is at or above ``NEAR_CAPACITY_PERCENT``
  of the warehouse's max but hasn't crossed it yet.
* ``near_low_inventory`` -- inventory is within ``NEAR_LOW_INVENTORY_BUFFER``
  boxes of the warehouse's minimum but still above it.
* ``box_stuck`` -- one or more boxes have been in 'received' state for
  longer than ``BOX_STUCK_THRESHOLD_DAYS``.

PostgreSQL only supports adding values to an enum, not removing them, so
the downgrade is a no-op (we keep the values in the type even if no row
references them).
"""
from __future__ import annotations

from alembic import op

revision = "0008_alert_type_extensions"
down_revision = "0007_alert_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'near_capacity'")
    op.execute(
        "ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'near_low_inventory'"
    )
    op.execute("ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'box_stuck'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values without recreating the type; we
    # leave the values in place because no row will reference them once the
    # corresponding ``Alert`` rows are deleted by the application.
    pass
