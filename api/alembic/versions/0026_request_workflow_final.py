"""expand request lifecycle and operational exception recovery

Revision ID: 0026_request_workflow_final
Revises: 0025_receipt_governance
Create Date: 2026-08-10 22:45:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026_request_workflow_final"
down_revision = "0025_receipt_governance"
branch_labels = None
depends_on = None

_STATUS_VALUES = (
    "preparing",
    "ready_for_transport",
    "awaiting_confirmation",
)
_EVENT_VALUES = (
    "preparation_started",
    "ready_for_transport",
    "awaiting_confirmation",
    "hold_started",
    "resumed",
    "rescheduled",
    "failed_delivery",
    "transport_retry",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"

    # PostgreSQL enum values must be committed before later statements can
    # reference them. Existing rows are deliberately not rewritten: nullable
    # milestone columns remain NULL unless the corresponding handoff happened.
    if is_postgresql:
        with op.get_context().autocommit_block():
            for value in _STATUS_VALUES:
                op.execute(
                    f"ALTER TYPE box_request_status ADD VALUE IF NOT EXISTS '{value}'"
                )
            for value in _EVENT_VALUES:
                op.execute(
                    f"ALTER TYPE box_request_event_type ADD VALUE IF NOT EXISTS '{value}'"
                )

    for name in (
        "preparing_by_user_id",
        "ready_for_transport_by_user_id",
        "awaiting_confirmation_by_user_id",
    ):
        op.add_column("box_requests", sa.Column(name, sa.Integer()))
        op.create_foreign_key(
            f"fk_box_requests_{name}_users",
            "box_requests",
            "users",
            [name],
            ["id"],
            ondelete="SET NULL",
        )
    for name in (
        "preparing_at",
        "ready_for_transport_at",
        "awaiting_confirmation_at",
    ):
        op.add_column(
            "box_requests",
            sa.Column(name, sa.DateTime(timezone=True)),
        )

    exception_kind_ddl = postgresql.ENUM(
        "hold",
        "reschedule",
        "failed_delivery",
        name="box_request_exception_kind",
    )
    if is_postgresql:
        exception_kind_ddl.create(bind, checkfirst=True)

    status_type: sa.types.TypeEngine[object]
    if is_postgresql:
        status_type = postgresql.ENUM(
            name="box_request_status",
            create_type=False,
        )
        exception_kind_type: sa.types.TypeEngine[object] = postgresql.ENUM(
            "hold",
            "reschedule",
            "failed_delivery",
            name="box_request_exception_kind",
            create_type=False,
        )
    else:
        status_type = sa.String(32)
        exception_kind_type = sa.String(32)

    op.create_table(
        "box_request_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("exception_kind", exception_kind_type, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("revised_window_start", sa.DateTime(timezone=True)),
        sa.Column("revised_window_end", sa.DateTime(timezone=True)),
        sa.Column("resume_target", status_type, nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "resolved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.Text()),
    )
    op.create_index(
        "ix_box_request_exceptions_request_resolution",
        "box_request_exceptions",
        ["request_id", "resolved_at", "created_at"],
    )
    op.create_index(
        "ix_box_request_exceptions_kind_created",
        "box_request_exceptions",
        ["exception_kind", "created_at"],
    )
    op.create_index(
        "ix_box_requests_status_transport_handoffs",
        "box_requests",
        ["status", "ready_for_transport_at", "awaiting_confirmation_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_box_requests_status_transport_handoffs",
        table_name="box_requests",
    )
    op.drop_index(
        "ix_box_request_exceptions_kind_created",
        table_name="box_request_exceptions",
    )
    op.drop_index(
        "ix_box_request_exceptions_request_resolution",
        table_name="box_request_exceptions",
    )
    op.drop_table("box_request_exceptions")
    for name in (
        "awaiting_confirmation_at",
        "ready_for_transport_at",
        "preparing_at",
    ):
        op.drop_column("box_requests", name)
    for name in (
        "awaiting_confirmation_by_user_id",
        "ready_for_transport_by_user_id",
        "preparing_by_user_id",
    ):
        op.drop_constraint(
            f"fk_box_requests_{name}_users",
            "box_requests",
            type_="foreignkey",
        )
        op.drop_column("box_requests", name)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(name="box_request_exception_kind").drop(
            bind,
            checkfirst=True,
        )
    # Existing PostgreSQL enum values are intentionally retained. Removing
    # values is unsafe when audit rows or historical statuses may reference them.
