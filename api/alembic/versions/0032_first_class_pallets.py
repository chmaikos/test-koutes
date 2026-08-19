"""add first-class audited pallets

Revision ID: 0032_first_class_pallets
Revises: 0031_return_target_warehouse
Create Date: 2026-08-19 21:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032_first_class_pallets"
down_revision = "0031_return_target_warehouse"
branch_labels = None
depends_on = None

_BOX_FK = "fk_boxes_pallet_id_pallets"
_BOX_INDEX = "ix_boxes_pallet_id"
_REQUEST_ITEM_FK = "fk_box_request_items_pallet_id_pallets"
_REQUEST_ITEM_INDEX = "ix_box_request_items_pallet_id"


def _create_tables() -> None:
    op.create_table(
        "pallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("current_warehouse_id", sa.Integer(), nullable=False),
        sa.Column("pallet_number", sa.String(length=64), nullable=False),
        sa.Column("normalized_pallet_number", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.CheckConstraint("version > 0", name="ck_pallets_version_positive"),
        sa.CheckConstraint(
            "(is_active AND archived_at IS NULL AND archived_by_user_id IS NULL) "
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
            ["current_warehouse_id"],
            ["warehouses.id"],
            name="fk_pallets_current_warehouse_id_warehouses",
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
    )
    op.create_index(
        "ix_pallets_warehouse_active",
        "pallets",
        ["current_warehouse_id", "is_active"],
    )
    op.create_index("ix_pallets_lot_active", "pallets", ["lot_id", "is_active"])
    op.create_index(
        "ix_pallets_absorbed_into",
        "pallets",
        ["absorbed_into_pallet_id"],
    )

    op.create_table(
        "pallet_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pallet_id", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "created",
                "renumbered",
                "archived",
                "restored",
                "moved",
                "lot_reassigned",
                "merged_absorbed",
                "boxes_assigned",
                "boxes_unassigned",
                name="pallet_event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("old_pallet_number", sa.String(length=64)),
        sa.Column("new_pallet_number", sa.String(length=64)),
        sa.Column("from_warehouse_id", sa.Integer()),
        sa.Column("to_warehouse_id", sa.Integer()),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "occurred_at",
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
        sa.ForeignKeyConstraint(
            ["pallet_id"],
            ["pallets.id"],
            name="fk_pallet_events_pallet_id_pallets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_warehouse_id"],
            ["warehouses.id"],
            name="fk_pallet_events_from_warehouse_id_warehouses",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_warehouse_id"],
            ["warehouses.id"],
            name="fk_pallet_events_to_warehouse_id_warehouses",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_pallet_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_pallet_events_pallet_occurred",
        "pallet_events",
        ["pallet_id", "occurred_at"],
    )
    op.create_index(
        "ix_pallet_events_actor_occurred",
        "pallet_events",
        ["actor_user_id", "occurred_at"],
    )


def upgrade() -> None:
    _create_tables()
    context = op.get_context()
    with op.batch_alter_table("lot_purge_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pallet_ids",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "pallet_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "pallet_snapshots",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_lot_purge_events_pallet_count_nonnegative",
            "pallet_count >= 0",
        )
    if context.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE box_event_type ADD VALUE IF NOT EXISTS 'pallet_assigned'"
        )
        op.execute(
            "ALTER TYPE box_event_type ADD VALUE IF NOT EXISTS 'pallet_unassigned'"
        )
    if context.dialect.name == "sqlite":
        with op.batch_alter_table("boxes") as batch_op:
            batch_op.add_column(sa.Column("pallet_id", sa.Integer(), nullable=True))
            batch_op.alter_column(
                "contents",
                existing_type=sa.String(length=200),
                type_=sa.Text(),
                existing_nullable=True,
            )
            batch_op.create_foreign_key(
                _BOX_FK,
                "pallets",
                ["pallet_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column("boxes", sa.Column("pallet_id", sa.Integer(), nullable=True))
        op.alter_column(
            "boxes",
            "contents",
            existing_type=sa.String(length=200),
            type_=sa.Text(),
            existing_nullable=True,
        )
        op.create_foreign_key(
            _BOX_FK,
            "boxes",
            "pallets",
            ["pallet_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(_BOX_INDEX, "boxes", ["pallet_id"])

    if context.dialect.name == "sqlite":
        with op.batch_alter_table("box_request_items") as batch_op:
            batch_op.add_column(sa.Column("pallet_id", sa.Integer(), nullable=True))
            batch_op.add_column(
                sa.Column("pallet", sa.String(length=64), nullable=True)
            )
            batch_op.alter_column(
                "contents",
                existing_type=sa.String(length=200),
                type_=sa.Text(),
                existing_nullable=True,
            )
            batch_op.create_foreign_key(
                _REQUEST_ITEM_FK,
                "pallets",
                ["pallet_id"],
                ["id"],
                ondelete="SET NULL",
            )
    else:
        op.add_column(
            "box_request_items",
            sa.Column("pallet_id", sa.Integer(), nullable=True),
        )
        op.add_column(
            "box_request_items",
            sa.Column("pallet", sa.String(length=64), nullable=True),
        )
        op.alter_column(
            "box_request_items",
            "contents",
            existing_type=sa.String(length=200),
            type_=sa.Text(),
            existing_nullable=True,
        )
        op.create_foreign_key(
            _REQUEST_ITEM_FK,
            "box_request_items",
            "pallets",
            ["pallet_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        _REQUEST_ITEM_INDEX,
        "box_request_items",
        ["pallet_id"],
    )


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    assigned_sql = "SELECT 1 FROM boxes WHERE pallet_id IS NOT NULL"
    pallet_sql = "SELECT 1 FROM pallets"
    purge_audit_sql = """
        SELECT 1
        FROM lot_purge_events
        WHERE pallet_count > 0
    """
    snapshot_sql = """
        SELECT 1
        FROM box_request_items
        WHERE pallet_id IS NOT NULL OR pallet IS NOT NULL
    """
    oversized_contents_sql = """
        SELECT 1
        FROM boxes
        WHERE length(contents) > 200
        UNION ALL
        SELECT 1
        FROM box_request_items
        WHERE length(contents) > 200
    """
    if context.dialect.name == "postgresql":
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS ({assigned_sql})
                   OR EXISTS ({pallet_sql})
                   OR EXISTS ({purge_audit_sql})
                   OR EXISTS ({snapshot_sql})
                   OR EXISTS ({oversized_contents_sql}) THEN
                    RAISE EXCEPTION
                        '0032 downgrade blocked: pallet data, audit snapshots, '
                        'assignments, request snapshots, or contents over 200 characters exist';
                END IF;
            END
            $$;
            """
        )
    elif not context.as_sql:
        bind = op.get_bind()
        if (
            bind.execute(sa.text(assigned_sql)).first() is not None
            or bind.execute(sa.text(pallet_sql)).first() is not None
            or bind.execute(sa.text(purge_audit_sql)).first() is not None
            or bind.execute(sa.text(snapshot_sql)).first() is not None
            or bind.execute(sa.text(oversized_contents_sql)).first() is not None
        ):
            raise RuntimeError(
                "0032 downgrade blocked: pallet data, audit snapshots, assignments, "
                "request snapshots, or contents over 200 characters exist"
            )


def downgrade() -> None:
    context = op.get_context()
    _assert_safe_downgrade()
    with op.batch_alter_table("lot_purge_events") as batch_op:
        batch_op.drop_constraint(
            "ck_lot_purge_events_pallet_count_nonnegative",
            type_="check",
        )
        batch_op.drop_column("pallet_snapshots")
        batch_op.drop_column("pallet_count")
        batch_op.drop_column("pallet_ids")
    op.drop_index(_REQUEST_ITEM_INDEX, table_name="box_request_items")
    if context.dialect.name == "sqlite":
        with op.batch_alter_table("box_request_items") as batch_op:
            batch_op.drop_constraint(_REQUEST_ITEM_FK, type_="foreignkey")
            batch_op.alter_column(
                "contents",
                existing_type=sa.Text(),
                type_=sa.String(length=200),
                existing_nullable=True,
            )
            batch_op.drop_column("pallet")
            batch_op.drop_column("pallet_id")
    else:
        op.drop_constraint(
            _REQUEST_ITEM_FK,
            "box_request_items",
            type_="foreignkey",
        )
        op.drop_column("box_request_items", "pallet")
        op.drop_column("box_request_items", "pallet_id")
        op.alter_column(
            "box_request_items",
            "contents",
            existing_type=sa.Text(),
            type_=sa.String(length=200),
            existing_nullable=True,
        )
    op.drop_index(_BOX_INDEX, table_name="boxes")
    if context.dialect.name == "sqlite":
        with op.batch_alter_table("boxes") as batch_op:
            batch_op.drop_constraint(_BOX_FK, type_="foreignkey")
            batch_op.alter_column(
                "contents",
                existing_type=sa.Text(),
                type_=sa.String(length=200),
                existing_nullable=True,
            )
            batch_op.drop_column("pallet_id")
    else:
        op.drop_constraint(_BOX_FK, "boxes", type_="foreignkey")
        op.drop_column("boxes", "pallet_id")
        op.alter_column(
            "boxes",
            "contents",
            existing_type=sa.Text(),
            type_=sa.String(length=200),
            existing_nullable=True,
        )
    op.drop_index("ix_pallet_events_actor_occurred", table_name="pallet_events")
    op.drop_index("ix_pallet_events_pallet_occurred", table_name="pallet_events")
    op.drop_table("pallet_events")
    op.drop_index("ix_pallets_absorbed_into", table_name="pallets")
    op.drop_index("ix_pallets_lot_active", table_name="pallets")
    op.drop_index("ix_pallets_warehouse_active", table_name="pallets")
    op.drop_table("pallets")
