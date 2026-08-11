"""add the force-purge request adjustment event

Revision ID: 0030_force_purge_adjustment
Revises: 0029_lot_purge_audit
Create Date: 2026-08-11 22:50:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030_force_purge_adjustment"
down_revision = "0029_lot_purge_audit"
branch_labels = None
depends_on = None

_EVENT_VALUE = "force_purge_adjusted"


def upgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        # ADD VALUE is idempotent and must be committed before application code
        # can write the new value on PostgreSQL versions that enforce the enum
        # visibility boundary.
        with context.autocommit_block():
            op.execute(
                "ALTER TYPE box_request_event_type "
                f"ADD VALUE IF NOT EXISTS '{_EVENT_VALUE}'"
            )
    # SQLite stores this SQLAlchemy enum as VARCHAR without an enum check, so
    # the model value is immediately usable and no table rebuild is required.


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM box_request_events
                    WHERE event_type = '{_EVENT_VALUE}'
                ) THEN
                    RAISE EXCEPTION
                        '0030 downgrade blocked: force purge adjustment events exist';
                END IF;
            END
            $$;
            """
        )
    elif not context.as_sql:
        has_events = op.get_bind().execute(
            sa.text(
                "SELECT EXISTS("
                "SELECT 1 FROM box_request_events "
                "WHERE event_type = :event_type"
                ")"
            ),
            {"event_type": _EVENT_VALUE},
        ).scalar()
        if has_events:
            raise RuntimeError(
                "0030 downgrade blocked: force purge adjustment events exist"
            )


def downgrade() -> None:
    _assert_safe_downgrade()
    # PostgreSQL enum labels cannot be removed safely in-place. Keeping an
    # unused value makes downgrade non-lossy; SQLite required no schema change.
