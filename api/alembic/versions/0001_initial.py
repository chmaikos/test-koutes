"""initial schema with seed warehouses

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-04 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


# Reference the enum types by name only. The actual CREATE TYPE statements are
# issued via raw SQL in upgrade(), which sidesteps SQLAlchemy's per-Column
# auto-creation logic (which can fire CREATE TYPE more than once when the same
# enum is reused across multiple tables).
USER_ROLE = postgresql.ENUM(
    "admin", "operator", "viewer", name="user_role", create_type=False
)
BOX_STATUS = postgresql.ENUM(
    "received",
    "in_progress",
    "processing_complete",
    "ready_to_return",
    "returned",
    name="box_status",
    create_type=False,
)
BOX_EVENT_TYPE = postgresql.ENUM(
    "created",
    "moved",
    "status_changed",
    "returned",
    name="box_event_type",
    create_type=False,
)
ALERT_TYPE = postgresql.ENUM(
    "low_inventory", "max_capacity", name="alert_type", create_type=False
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE user_role AS ENUM ('admin', 'operator', 'viewer')"
    )
    op.execute(
        "CREATE TYPE box_status AS ENUM ("
        "'received', 'in_progress', 'processing_complete', "
        "'ready_to_return', 'returned')"
    )
    op.execute(
        "CREATE TYPE box_event_type AS ENUM ("
        "'created', 'moved', 'status_changed', 'returned')"
    )
    op.execute(
        "CREATE TYPE alert_type AS ENUM ('low_inventory', 'max_capacity')"
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entra_oid", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("role", USER_ROLE, nullable=False, server_default="viewer"),
        sa.Column(
            "role_override", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_entra_oid", "users", ["entra_oid"], unique=True)
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("min_inventory", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_capacity", sa.Integer(), nullable=False, server_default="1000"),
    )

    op.create_table(
        "boxes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("box_number", sa.String(64), nullable=False, unique=True),
        sa.Column("owner", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "current_warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", BOX_STATUS, nullable=False, server_default="received"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
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
            "updated_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_boxes_box_number", "boxes", ["box_number"], unique=True)
    op.create_index("ix_boxes_current_warehouse_id", "boxes", ["current_warehouse_id"])
    op.create_index(
        "ix_boxes_status_warehouse", "boxes", ["status", "current_warehouse_id"]
    )

    op.create_table(
        "box_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "box_id",
            sa.Integer(),
            sa.ForeignKey("boxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", BOX_EVENT_TYPE, nullable=False),
        sa.Column("from_status", BOX_STATUS, nullable=True),
        sa.Column("to_status", BOX_STATUS, nullable=True),
        sa.Column(
            "from_warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_box_events_warehouse_occurred",
        "box_events",
        ["warehouse_id", "occurred_at"],
    )
    op.create_index(
        "ix_box_events_box_occurred", "box_events", ["box_id", "occurred_at"]
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", ALERT_TYPE, nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acknowledged_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_alerts_warehouse_id", "alerts", ["warehouse_id"])

    op.bulk_insert(
        sa.table(
            "warehouses",
            sa.column("id", sa.Integer),
            sa.column("name", sa.String),
            sa.column("min_inventory", sa.Integer),
            sa.column("max_capacity", sa.Integer),
        ),
        [
            {"id": 1, "name": "Building 1", "min_inventory": 0, "max_capacity": 500},
            {"id": 2, "name": "Building 2", "min_inventory": 0, "max_capacity": 500},
            {"id": 3, "name": "Building 3", "min_inventory": 0, "max_capacity": 500},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_warehouse_id", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("ix_box_events_box_occurred", table_name="box_events")
    op.drop_index("ix_box_events_warehouse_occurred", table_name="box_events")
    op.drop_table("box_events")
    op.drop_index("ix_boxes_status_warehouse", table_name="boxes")
    op.drop_index("ix_boxes_current_warehouse_id", table_name="boxes")
    op.drop_index("ix_boxes_box_number", table_name="boxes")
    op.drop_table("boxes")
    op.drop_table("warehouses")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_entra_oid", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS alert_type")
    op.execute("DROP TYPE IF EXISTS box_event_type")
    op.execute("DROP TYPE IF EXISTS box_status")
    op.execute("DROP TYPE IF EXISTS user_role")
