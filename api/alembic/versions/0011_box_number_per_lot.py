"""make boxes.box_number unique only per lot, pad existing numeric values

Revision ID: 0011_box_number_per_lot
Revises: 0010_box_status_extensions
Create Date: 2026-05-11 20:30:00.000000

The original schema (see ``0001_initial``) declared ``boxes.box_number`` as
globally unique, which is wrong in practice: the same number routinely
appears in different lots (operators reuse 001..050 for each fresh lot).
This migration relaxes the rule so the uniqueness key is
``(lot, box_number)`` instead.

In the same step we standardise existing numeric values to the new
three-digit minimum form (``'1'`` -> ``'001'``, ``'42'`` -> ``'042'``,
values longer than 3 digits stay as-is). Legacy non-numeric ``box_number``
strings (created before the API started enforcing numeric input) are left
alone so the migration never fails on existing data; the API validator
only applies to new writes from now on.
"""
from __future__ import annotations

from alembic import op

revision = "0011_box_number_per_lot"
down_revision = "0010_box_status_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pad existing purely-numeric box numbers up to the new 3-char minimum
    # before swapping the constraints. Done first so any (lot, box_number)
    # collisions surface as the constraint-creation failure below rather
    # than a silent data-shape change later.
    op.execute(
        "UPDATE boxes SET box_number = lpad(box_number, 3, '0') "
        "WHERE box_number ~ '^[0-9]+$' AND char_length(box_number) < 3"
    )

    # Drop the old global uniqueness: the auto-generated UNIQUE constraint
    # SQLAlchemy created from ``unique=True`` on the column, plus the
    # explicit unique index from ``0001_initial``.
    op.drop_constraint("boxes_box_number_key", "boxes", type_="unique")
    op.drop_index("ix_boxes_box_number", table_name="boxes")

    # Recreate as a plain (non-unique) index so the ``ilike`` search in
    # routers/_filters.py keeps using an index scan.
    op.create_index(
        "ix_boxes_box_number", "boxes", ["box_number"], unique=False
    )

    # The new uniqueness key: same number is allowed across lots, but only
    # once within a single lot. Postgres enforces this with a unique btree
    # index under the hood, so the search/sort cost is the same.
    op.create_unique_constraint(
        "uq_boxes_lot_box_number", "boxes", ["lot", "box_number"]
    )


def downgrade() -> None:
    # Reverse the constraint swap. Note: this will raise if the table has
    # acquired any duplicate ``box_number`` values across lots since the
    # upgrade ran, which is the expected behaviour for a rollback (the
    # operator has to decide which duplicates to keep).
    op.drop_constraint("uq_boxes_lot_box_number", "boxes", type_="unique")
    op.drop_index("ix_boxes_box_number", table_name="boxes")
    op.create_index(
        "ix_boxes_box_number", "boxes", ["box_number"], unique=True
    )
    op.create_unique_constraint(
        "boxes_box_number_key", "boxes", ["box_number"]
    )
