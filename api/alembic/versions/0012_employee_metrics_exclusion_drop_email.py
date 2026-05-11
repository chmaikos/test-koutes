"""employees: add excluded_from_metrics on employees + productivity_entries; drop employees.email

Revision ID: 0012_employee_metrics_exclusion_drop_email
Revises: 0011_box_number_per_lot
Create Date: 2026-05-11 23:25:00.000000

Two roster-level changes shipped together so the schema move is atomic:

* ``employees.excluded_from_metrics`` -- admin override that pulls an
  individual employee out of every productivity aggregate (totals, top
  and bottom leaderboards). Useful for special-case roles (trainers,
  off-floor staff) whose work shouldn't dilute the floor crew's
  averages. The roster row stays visible and editable; only the math
  changes.
* ``productivity_entries.excluded_from_metrics`` -- per-entry opt-out
  the operator can toggle when adding a row that shouldn't count
  (training run, equipment failure, partial shift). Stored on the entry
  rather than computed so it's auditable after the fact.
* ``employees.email`` is dropped. The column was unused outside the
  Settings UI and the productivity email reports route through application
  users instead. Removing it shrinks the surface area and lets the
  Settings page focus on attributes that actually drive behaviour.

Both new booleans are ``NOT NULL DEFAULT FALSE`` so existing rows
default to "count me in", which matches the prior behaviour exactly --
no data backfill required.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_employee_metrics_exclusion_drop_email"
down_revision = "0011_box_number_per_lot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column(
            "excluded_from_metrics",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "productivity_entries",
        sa.Column(
            "excluded_from_metrics",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.drop_column("employees", "email")


def downgrade() -> None:
    # Re-create the email column as nullable so a rollback succeeds even
    # though the old data is gone -- the application code is responsible
    # for repopulating it if the rollback is permanent.
    op.add_column(
        "employees",
        sa.Column("email", sa.String(length=320), nullable=True),
    )
    op.drop_column("productivity_entries", "excluded_from_metrics")
    op.drop_column("employees", "excluded_from_metrics")
