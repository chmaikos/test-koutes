"""add an independent lot purge audit ledger

Revision ID: 0029_lot_purge_audit
Revises: 0028_safe_lot_merge
Create Date: 2026-08-11 21:42:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_lot_purge_audit"
down_revision = "0028_safe_lot_merge"
branch_labels = None
depends_on = None


_CLEANUP_STATUSES = (
    "pending",
    "not_required",
    "in_progress",
    "completed",
    "partial_failure",
    "failed",
)


def upgrade() -> None:
    op.create_table(
        "lot_purge_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Snapshot only. There must never be a FK to the Lot being removed.
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("lot_name", sa.String(length=64), nullable=False),
        sa.Column("lot_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "receipt_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("receipt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "archived_box_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("archived_box_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "object_keys",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("object_key_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "object_cleanup_status",
            sa.Enum(
                *_CLEANUP_STATUSES,
                name="lot_purge_cleanup_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "object_cleanup_failures",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "object_cleanup_completed_at",
            sa.DateTime(timezone=True),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint(
            "lot_version > 0",
            name="ck_lot_purge_events_version_positive",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_lot_purge_events_reason_not_blank",
        ),
        sa.CheckConstraint(
            "receipt_count >= 0",
            name="ck_lot_purge_events_receipt_count_nonnegative",
        ),
        sa.CheckConstraint(
            "archived_box_count >= 0",
            name="ck_lot_purge_events_box_count_nonnegative",
        ),
        sa.CheckConstraint(
            "object_key_count >= 0",
            name="ck_lot_purge_events_object_key_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_lot_purge_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_lot_purge_events_lot_created",
        "lot_purge_events",
        ["lot_id", "created_at"],
    )
    op.create_index(
        "ix_lot_purge_events_actor_created",
        "lot_purge_events",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_lot_purge_events_cleanup_updated",
        "lot_purge_events",
        ["object_cleanup_status", "updated_at"],
    )


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM lot_purge_events) THEN
                    RAISE EXCEPTION
                        '0029 downgrade blocked: lot purge audit data exists';
                END IF;
            END
            $$;
            """
        )
    elif not context.as_sql:
        has_data = op.get_bind().execute(
            sa.text("SELECT EXISTS(SELECT 1 FROM lot_purge_events)")
        ).scalar()
        if has_data:
            raise RuntimeError(
                "0029 downgrade blocked: lot purge audit data exists"
            )


def downgrade() -> None:
    _assert_safe_downgrade()
    op.drop_index(
        "ix_lot_purge_events_cleanup_updated",
        table_name="lot_purge_events",
    )
    op.drop_index(
        "ix_lot_purge_events_actor_created",
        table_name="lot_purge_events",
    )
    op.drop_index(
        "ix_lot_purge_events_lot_created",
        table_name="lot_purge_events",
    )
    op.drop_table("lot_purge_events")
