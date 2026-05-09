"""alert lifecycle: notification log, escalated_at, email opt-out

Revision ID: 0007_alert_lifecycle
Revises: 0006_box_lot_contents
Create Date: 2026-05-09 14:00:00.000000

* Adds ``alert_notifications`` for an append-only audit log of every alert
  email we attempt to send. A row is written even when the send fails so
  the dispatcher can retry safely; the (alert_id, sent_at) index is what
  the lifecycle code uses to decide whether a reminder/escalation is due.
* Adds ``alerts.escalated_at`` so the dispatcher can stamp the moment an
  alert is escalated and never re-escalate the same one.
* Adds ``users.email_alerts_enabled`` (default true) so individual users
  can opt out without losing their warehouse ACL.

The ``alert_notification_kind`` enum mirrors ``AlertNotificationKind`` in
``app.models.alerts``.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_alert_lifecycle"
down_revision = "0006_box_lot_contents"
branch_labels = None
depends_on = None


ALERT_NOTIFICATION_KIND = postgresql.ENUM(
    "triggered",
    "reminder",
    "escalated",
    "resolved",
    "test",
    name="alert_notification_kind",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE alert_notification_kind AS ENUM ("
        "'triggered', 'reminder', 'escalated', 'resolved', 'test')"
    )

    op.add_column(
        "alerts",
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column(
        "users",
        sa.Column(
            "email_alerts_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    op.create_table(
        "alert_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_id",
            sa.Integer(),
            sa.ForeignKey("alerts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", ALERT_NOTIFICATION_KIND, nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("recipients", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "ok", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_alert_notifications_alert_sent",
        "alert_notifications",
        ["alert_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_notifications_alert_sent", table_name="alert_notifications"
    )
    op.drop_table("alert_notifications")

    op.drop_column("users", "email_alerts_enabled")
    op.drop_column("alerts", "escalated_at")

    op.execute("DROP TYPE IF EXISTS alert_notification_kind")
