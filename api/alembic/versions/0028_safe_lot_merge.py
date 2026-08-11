"""preserve merged lots as auditable tombstones

Revision ID: 0028_safe_lot_merge
Revises: 0027_first_class_lots
Create Date: 2026-08-11 21:15:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_safe_lot_merge"
down_revision = "0027_first_class_lots"
branch_labels = None
depends_on = None


def _replace_lot_event_constraint(values: tuple[str, ...]) -> None:
    constraint = sa.CheckConstraint(
        "event_type IN ({})".format(", ".join(f"'{value}'" for value in values)),
        name="lot_event_type",
    )
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("lot_events") as batch_op:
            batch_op.drop_constraint("lot_event_type", type_="check")
            batch_op.create_check_constraint(constraint.name, constraint.sqltext)
    else:
        op.drop_constraint("lot_event_type", "lot_events", type_="check")
        op.create_check_constraint(
            constraint.name,
            "lot_events",
            constraint.sqltext,
        )


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table("lots") as batch_op:
            batch_op.alter_column(
                "normalized_name",
                existing_type=sa.String(length=64),
                nullable=True,
            )
            batch_op.add_column(sa.Column("merged_into_lot_id", sa.Integer()))
            batch_op.add_column(sa.Column("merged_at", sa.DateTime(timezone=True)))
            batch_op.add_column(sa.Column("merged_by_user_id", sa.Integer()))
            batch_op.create_foreign_key(
                "fk_lots_merged_into_lot_id_lots",
                "lots",
                ["merged_into_lot_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_lots_merged_by_user_id_users",
                "users",
                ["merged_by_user_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_check_constraint(
                "ck_lots_merge_tombstone",
                """
                (
                    merged_into_lot_id IS NULL
                    AND merged_at IS NULL
                    AND merged_by_user_id IS NULL
                    AND normalized_name IS NOT NULL
                )
                OR
                (
                    merged_into_lot_id IS NOT NULL
                    AND merged_at IS NOT NULL
                    AND normalized_name IS NULL
                    AND merged_into_lot_id <> id
                )
                """,
            )
    else:
        op.alter_column(
            "lots",
            "normalized_name",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        op.add_column("lots", sa.Column("merged_into_lot_id", sa.Integer()))
        op.add_column("lots", sa.Column("merged_at", sa.DateTime(timezone=True)))
        op.add_column("lots", sa.Column("merged_by_user_id", sa.Integer()))
        op.create_foreign_key(
            "fk_lots_merged_into_lot_id_lots",
            "lots",
            "lots",
            ["merged_into_lot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            "fk_lots_merged_by_user_id_users",
            "lots",
            "users",
            ["merged_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_check_constraint(
            "ck_lots_merge_tombstone",
            "lots",
            """
            (
                merged_into_lot_id IS NULL
                AND merged_at IS NULL
                AND merged_by_user_id IS NULL
                AND normalized_name IS NOT NULL
            )
            OR
            (
                merged_into_lot_id IS NOT NULL
                AND merged_at IS NOT NULL
                AND normalized_name IS NULL
                AND merged_into_lot_id <> id
            )
            """,
        )

    op.create_index(
        "ix_lots_merged_into_lot_id",
        "lots",
        ["merged_into_lot_id"],
    )
    _replace_lot_event_constraint(("created", "renamed", "reassigned", "merged"))


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM lots WHERE merged_into_lot_id IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM lot_events WHERE event_type = 'merged'
                ) THEN
                    RAISE EXCEPTION
                        '0028 downgrade blocked: merged lot audit data exists';
                END IF;
            END
            $$;
            """
        )
    elif not context.as_sql:
        bind = op.get_bind()
        has_data = bind.execute(
            sa.text(
                """
                SELECT
                    EXISTS(SELECT 1 FROM lots WHERE merged_into_lot_id IS NOT NULL)
                    OR EXISTS(SELECT 1 FROM lot_events WHERE event_type = 'merged')
                """
            )
        ).scalar()
        if has_data:
            raise RuntimeError(
                "0028 downgrade blocked: merged lot audit data exists"
            )


def downgrade() -> None:
    _assert_safe_downgrade()
    _replace_lot_event_constraint(("created", "renamed", "reassigned"))
    op.drop_index("ix_lots_merged_into_lot_id", table_name="lots")

    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("lots") as batch_op:
            # SQLAlchemy's SQLite reflector cannot parse this multi-column
            # CHECK expression. Batch recreation therefore omits it
            # automatically when the referenced merge columns are removed.
            batch_op.drop_constraint(
                "fk_lots_merged_by_user_id_users",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                "fk_lots_merged_into_lot_id_lots",
                type_="foreignkey",
            )
            batch_op.drop_column("merged_by_user_id")
            batch_op.drop_column("merged_at")
            batch_op.drop_column("merged_into_lot_id")
            batch_op.alter_column(
                "normalized_name",
                existing_type=sa.String(length=64),
                nullable=False,
            )
    else:
        op.drop_constraint(
            "ck_lots_merge_tombstone",
            "lots",
            type_="check",
        )
        op.drop_constraint(
            "fk_lots_merged_by_user_id_users",
            "lots",
            type_="foreignkey",
        )
        op.drop_constraint(
            "fk_lots_merged_into_lot_id_lots",
            "lots",
            type_="foreignkey",
        )
        op.drop_column("lots", "merged_by_user_id")
        op.drop_column("lots", "merged_at")
        op.drop_column("lots", "merged_into_lot_id")
        op.alter_column(
            "lots",
            "normalized_name",
            existing_type=sa.String(length=64),
            nullable=False,
        )
