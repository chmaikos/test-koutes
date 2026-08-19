"""remove physical warehouse ownership from pallets

Revision ID: 0033_organizational_pallets
Revises: 0032_first_class_pallets
Create Date: 2026-08-19 23:55:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033_organizational_pallets"
down_revision = "0032_first_class_pallets"
branch_labels = None
depends_on = None

_WAREHOUSE_FK = "fk_pallets_current_warehouse_id_warehouses"
_WAREHOUSE_INDEX = "ix_pallets_warehouse_active"


def _pallets_table(
    *,
    include_warehouse: bool,
    warehouse_nullable: bool = False,
    include_warehouse_index: bool = False,
) -> sa.Table:
    """Describe the 0032/0033 table so SQLite batch mode also works offline."""
    metadata = sa.MetaData()
    columns: list[sa.SchemaItem] = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), nullable=False),
    ]
    if include_warehouse:
        columns.append(
            sa.Column(
                "current_warehouse_id",
                sa.Integer(),
                nullable=warehouse_nullable,
            )
        )
    columns.extend(
        [
            sa.Column("pallet_number", sa.String(length=64), nullable=False),
            sa.Column(
                "normalized_pallet_number",
                sa.String(length=64),
                nullable=False,
            ),
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column("archived_at", sa.DateTime(timezone=True)),
            sa.Column("archived_by_user_id", sa.Integer()),
            sa.Column("archive_reason", sa.Text()),
            sa.Column("absorbed_into_pallet_id", sa.Integer()),
            sa.Column("absorbed_at", sa.DateTime(timezone=True)),
            sa.Column("absorbed_by_user_id", sa.Integer()),
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
            sa.Column("created_by_user_id", sa.Integer()),
            sa.Column("updated_by_user_id", sa.Integer()),
            sa.CheckConstraint(
                "pallet_number <> ''",
                name="ck_pallets_number_not_blank",
            ),
            sa.CheckConstraint(
                "normalized_pallet_number <> ''",
                name="ck_pallets_normalized_number_not_blank",
            ),
            sa.CheckConstraint(
                "version > 0",
                name="ck_pallets_version_positive",
            ),
            sa.CheckConstraint(
                "(is_active AND archived_at IS NULL "
                "AND archived_by_user_id IS NULL) "
                "OR (NOT is_active AND archived_at IS NOT NULL)",
                name="ck_pallets_archive_state",
            ),
            sa.CheckConstraint(
                "(absorbed_into_pallet_id IS NULL AND absorbed_at IS NULL "
                "AND absorbed_by_user_id IS NULL) "
                "OR (NOT is_active AND absorbed_into_pallet_id IS NOT NULL "
                "AND absorbed_at IS NOT NULL)",
                name="ck_pallets_absorbed_state",
            ),
            sa.ForeignKeyConstraint(
                ["lot_id"],
                ["lots.id"],
                name="fk_pallets_lot_id_lots",
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["archived_by_user_id"],
                ["users.id"],
                name="fk_pallets_archived_by_user_id_users",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["absorbed_into_pallet_id"],
                ["pallets.id"],
                name="fk_pallets_absorbed_into_pallet_id_pallets",
                ondelete="RESTRICT",
            ),
            sa.ForeignKeyConstraint(
                ["absorbed_by_user_id"],
                ["users.id"],
                name="fk_pallets_absorbed_by_user_id_users",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["created_by_user_id"],
                ["users.id"],
                name="fk_pallets_created_by_user_id_users",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["updated_by_user_id"],
                ["users.id"],
                name="fk_pallets_updated_by_user_id_users",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "lot_id",
                "normalized_pallet_number",
                name="uq_pallets_lot_normalized_number",
            ),
        ]
    )
    if include_warehouse:
        columns.append(
            sa.ForeignKeyConstraint(
                ["current_warehouse_id"],
                ["warehouses.id"],
                name=_WAREHOUSE_FK,
                ondelete="RESTRICT",
            )
        )
    table = sa.Table("pallets", metadata, *columns)
    sa.Index("ix_pallets_lot_active", table.c.lot_id, table.c.is_active)
    sa.Index("ix_pallets_absorbed_into", table.c.absorbed_into_pallet_id)
    if include_warehouse_index:
        sa.Index(
            _WAREHOUSE_INDEX,
            table.c.current_warehouse_id,
            table.c.is_active,
        )
    return table


def upgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "sqlite":
        with op.batch_alter_table(
            "pallets",
            copy_from=_pallets_table(
                include_warehouse=True,
                include_warehouse_index=True,
            ),
            recreate="always",
        ) as batch_op:
            batch_op.drop_index(_WAREHOUSE_INDEX)
            batch_op.drop_constraint(_WAREHOUSE_FK, type_="foreignkey")
            batch_op.drop_column("current_warehouse_id")
        return

    op.drop_index(_WAREHOUSE_INDEX, table_name="pallets")
    op.drop_constraint(_WAREHOUSE_FK, "pallets", type_="foreignkey")
    op.drop_column("pallets", "current_warehouse_id")


_UNSAFE_DOWNGRADE_SQL = """
    SELECT p.id
    FROM pallets AS p
    LEFT JOIN boxes AS b ON b.pallet_id = p.id
    GROUP BY p.id
    HAVING COUNT(b.id) = 0
       OR COUNT(DISTINCT b.current_warehouse_id) <> 1
"""


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        if context.dialect.name == "postgresql":
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS ({_UNSAFE_DOWNGRADE_SQL}) THEN
                        RAISE EXCEPTION
                            '0033 downgrade blocked: every pallet must have '
                            'assigned boxes in exactly one warehouse';
                    END IF;
                END
                $$;
                """
            )
        elif context.dialect.name == "sqlite":
            op.execute(
                "CREATE TEMP TABLE _0033_downgrade_guard "
                "(unsafe INTEGER CHECK (unsafe = 0))"
            )
            op.execute(
                "INSERT INTO _0033_downgrade_guard (unsafe) "
                f"SELECT CASE WHEN EXISTS ({_UNSAFE_DOWNGRADE_SQL}) "
                "THEN 1 ELSE 0 END"
            )
            op.execute("DROP TABLE _0033_downgrade_guard")
        return

    unsafe = op.get_bind().execute(sa.text(_UNSAFE_DOWNGRADE_SQL)).first()
    if unsafe is not None:
        raise RuntimeError(
            "0033 downgrade blocked: every pallet must have assigned boxes "
            "in exactly one warehouse"
        )


def _populate_warehouse() -> None:
    op.execute(
        """
        UPDATE pallets
        SET current_warehouse_id = (
            SELECT MIN(boxes.current_warehouse_id)
            FROM boxes
            WHERE boxes.pallet_id = pallets.id
        )
        """
    )


def downgrade() -> None:
    context = op.get_context()
    _assert_safe_downgrade()
    if context.dialect.name == "sqlite":
        with op.batch_alter_table(
            "pallets",
            copy_from=_pallets_table(include_warehouse=False),
            recreate="always",
        ) as batch_op:
            batch_op.add_column(
                sa.Column("current_warehouse_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                _WAREHOUSE_FK,
                "warehouses",
                ["current_warehouse_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        _populate_warehouse()
        with op.batch_alter_table(
            "pallets",
            copy_from=_pallets_table(
                include_warehouse=True,
                warehouse_nullable=True,
            ),
            recreate="always",
        ) as batch_op:
            batch_op.alter_column(
                "current_warehouse_id",
                existing_type=sa.Integer(),
                nullable=False,
            )
        op.create_index(
            _WAREHOUSE_INDEX,
            "pallets",
            ["current_warehouse_id", "is_active"],
        )
        return

    op.add_column(
        "pallets",
        sa.Column("current_warehouse_id", sa.Integer(), nullable=True),
    )
    _populate_warehouse()
    op.alter_column(
        "pallets",
        "current_warehouse_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        _WAREHOUSE_FK,
        "pallets",
        "warehouses",
        ["current_warehouse_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        _WAREHOUSE_INDEX,
        "pallets",
        ["current_warehouse_id", "is_active"],
    )
