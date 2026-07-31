"""archive linked boxes and record receipt provenance

Revision ID: 0016_request_box_integrity
Revises: 0015_inbound_linked_returns
Create Date: 2026-07-31 13:14:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_request_box_integrity"
down_revision = "0015_inbound_linked_returns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE box_event_type ADD VALUE IF NOT EXISTS 'archived'")
    origin = postgresql.ENUM(
        "workflow",
        "xlsx_import",
        "manual_entry",
        "legacy_backfill",
        name="box_request_origin",
        create_type=False,
    )
    origin.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "boxes",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "boxes",
        sa.Column("archived_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column("boxes", sa.Column("archive_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_boxes_archived_by_user_id",
        "boxes",
        "users",
        ["archived_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_boxes_archived_at", "boxes", ["archived_at"])

    op.add_column(
        "box_requests",
        sa.Column(
            "origin",
            origin,
            nullable=False,
            server_default="workflow",
        ),
    )
    op.alter_column(
        "box_requests",
        "requester_user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    # Existing direct/manual/imported inventory has no inbound provenance.
    # Create one explicit system receipt per warehouse so those boxes can use
    # the same linked return workflow without fabricating mover approvals.
    op.execute(
        """
        DO $$
        DECLARE
            warehouse_row RECORD;
            receipt_id INTEGER;
        BEGIN
            FOR warehouse_row IN
                SELECT b.current_warehouse_id AS warehouse_id, COUNT(*)::INTEGER AS box_count
                FROM boxes b
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM box_request_items bri
                    JOIN box_requests br ON br.id = bri.request_id
                    WHERE bri.box_id = b.id
                      AND br.direction = 'inbound'
                )
                GROUP BY b.current_warehouse_id
            LOOP
                INSERT INTO box_requests (
                    direction,
                    warehouse_id,
                    quantity,
                    status,
                    requester_user_id,
                    origin,
                    suggestion_quantity,
                    current_available,
                    min_inventory,
                    pending_inbound,
                    eligible_return,
                    actual_received_quantity,
                    variance_quantity,
                    submitted_at,
                    completed_at,
                    created_at,
                    updated_at,
                    version
                )
                VALUES (
                    'inbound',
                    warehouse_row.warehouse_id,
                    warehouse_row.box_count,
                    'completed',
                    NULL,
                    'legacy_backfill',
                    warehouse_row.box_count,
                    0,
                    0,
                    0,
                    0,
                    warehouse_row.box_count,
                    0,
                    NOW(),
                    NOW(),
                    NOW(),
                    NOW(),
                    1
                )
                RETURNING id INTO receipt_id;

                INSERT INTO box_request_items (
                    request_id,
                    position,
                    box_id,
                    lot,
                    box_number,
                    contents
                )
                SELECT
                    receipt_id,
                    ROW_NUMBER() OVER (ORDER BY b.created_at, b.id)::INTEGER,
                    b.id,
                    b.lot,
                    b.box_number,
                    b.contents
                FROM boxes b
                WHERE b.current_warehouse_id = warehouse_row.warehouse_id
                  AND NOT EXISTS (
                      SELECT 1
                      FROM box_request_items bri
                      JOIN box_requests br ON br.id = bri.request_id
                      WHERE bri.box_id = b.id
                        AND br.direction = 'inbound'
                  );

                INSERT INTO box_request_events (
                    request_id,
                    event_type,
                    from_status,
                    to_status,
                    user_id,
                    note,
                    occurred_at
                )
                VALUES (
                    receipt_id,
                    'completed',
                    NULL,
                    'completed',
                    NULL,
                    'System-generated legacy receipt for inventory '
                    || 'that predates request provenance.',
                    NOW()
                );
            END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM box_requests WHERE origin = 'legacy_backfill'")
    op.alter_column(
        "box_requests",
        "requester_user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.drop_column("box_requests", "origin")
    postgresql.ENUM(name="box_request_origin").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_boxes_archived_at", table_name="boxes")
    op.drop_constraint(
        "fk_boxes_archived_by_user_id",
        "boxes",
        type_="foreignkey",
    )
    op.drop_column("boxes", "archive_reason")
    op.drop_column("boxes", "archived_by_user_id")
    op.drop_column("boxes", "archived_at")
