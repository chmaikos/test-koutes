"""add the return request target warehouse

Revision ID: 0031_return_target_warehouse
Revises: 0030_force_purge_adjustment
Create Date: 2026-08-18 20:55:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_return_target_warehouse"
down_revision = "0030_force_purge_adjustment"
branch_labels = None
depends_on = None

_CHECK_NAME = "ck_box_requests_direction_target_warehouse"
_FK_NAME = "fk_box_requests_target_warehouse_id"
_INDEX_NAME = "ix_box_requests_target_warehouse_id"


def _assert_direction_contract() -> None:
    context = op.get_context()
    invalid_sql = """
        SELECT 1
        FROM box_requests
        WHERE NOT (
            (direction = 'return' AND target_warehouse_id IS NOT NULL)
            OR (direction = 'inbound' AND target_warehouse_id IS NULL)
        )
    """
    if context.dialect.name == "postgresql":
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS ({invalid_sql}) THEN
                    RAISE EXCEPTION
                        '0031 upgrade blocked: request directions do not satisfy '
                        'the target warehouse contract';
                END IF;
            END
            $$;
            """
        )
    elif not context.as_sql:
        if op.get_bind().execute(sa.text(invalid_sql)).first() is not None:
            raise RuntimeError(
                "0031 upgrade blocked: request directions do not satisfy "
                "the target warehouse contract"
            )


def upgrade() -> None:
    context = op.get_context()
    op.add_column(
        "box_requests",
        sa.Column("target_warehouse_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE box_requests
        SET target_warehouse_id = warehouse_id
        WHERE direction = 'return'
        """
    )
    _assert_direction_contract()

    if context.dialect.name == "sqlite":
        with op.batch_alter_table("box_requests") as batch_op:
            batch_op.create_foreign_key(
                _FK_NAME,
                "warehouses",
                ["target_warehouse_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_check_constraint(
                _CHECK_NAME,
                "(direction = 'return' AND target_warehouse_id IS NOT NULL) OR "
                "(direction = 'inbound' AND target_warehouse_id IS NULL)",
            )
    else:
        op.create_foreign_key(
            _FK_NAME,
            "box_requests",
            "warehouses",
            ["target_warehouse_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_check_constraint(
            _CHECK_NAME,
            "box_requests",
            "(direction = 'return' AND target_warehouse_id IS NOT NULL) OR "
            "(direction = 'inbound' AND target_warehouse_id IS NULL)",
        )
    op.create_index(
        _INDEX_NAME,
        "box_requests",
        ["target_warehouse_id"],
        unique=False,
    )


def _assert_safe_downgrade() -> None:
    context = op.get_context()
    lossy_sql = """
        SELECT 1
        FROM box_requests
        WHERE target_warehouse_id IS NOT NULL
          AND target_warehouse_id <> warehouse_id
    """
    if context.dialect.name == "postgresql":
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS ({lossy_sql}) THEN
                    RAISE EXCEPTION
                        '0031 downgrade blocked: cross-warehouse return targets exist';
                END IF;
            END
            $$;
            """
        )
    elif not context.as_sql:
        if op.get_bind().execute(sa.text(lossy_sql)).first() is not None:
            raise RuntimeError(
                "0031 downgrade blocked: cross-warehouse return targets exist"
            )


def downgrade() -> None:
    context = op.get_context()
    _assert_safe_downgrade()
    op.drop_index(_INDEX_NAME, table_name="box_requests")
    if context.dialect.name == "sqlite":
        with op.batch_alter_table("box_requests") as batch_op:
            batch_op.drop_constraint(_CHECK_NAME, type_="check")
            batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
            batch_op.drop_column("target_warehouse_id")
    else:
        op.drop_constraint(_CHECK_NAME, "box_requests", type_="check")
        op.drop_constraint(_FK_NAME, "box_requests", type_="foreignkey")
        op.drop_column("box_requests", "target_warehouse_id")
