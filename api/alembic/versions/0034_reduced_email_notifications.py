"""persist the reduced email notification policy

Revision ID: 0034_reduced_email_notifications
Revises: 0033_organizational_pallets
Create Date: 2026-09-15 23:58:00.000000

This migration closes obsolete open ``box_stuck`` alerts, reconciles any
other duplicate open alert incidents, adds a recoverable opening-email claim,
adds a recoverable request-outbox claim, and discards pending request-email
work that is outside the reduced policy. No alert, notification, or outbox
row is deleted.

The data changes are intentionally irreversible. Downgrade removes only the
new schema objects; it does not reopen alerts or reconstruct discarded email
intent after the discard audit columns are removed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034_reduced_email_notifications"
down_revision = "0033_organizational_pallets"
branch_labels = None
depends_on = None

_ALERT_OPEN_INDEX = "uq_alerts_open_warehouse_type"
_OUTBOX_DISCARD_CHECK = "ck_request_email_outbox_discard_consistency"
_OUTBOX_DISPATCH_INDEX = "ix_request_email_outbox_dispatch_pending"
_DEPLOYMENT_TIMESTAMP_TABLE = "_0034_deployment_timestamp"
_ALERT_DUPLICATE_MAP_TABLE = "_0034_alert_duplicate_map"
_ALLOWED_REQUEST_EMAIL_KINDS = (
    "staged",
    "submitted",
    "approved",
    "confirmation_received",
)


def _request_email_outbox_table(
    *,
    include_discard_fields: bool,
) -> sa.Table:
    """Describe the table for deterministic SQLite offline batch operations."""
    metadata = sa.MetaData()
    sa.Table("box_requests", metadata, sa.Column("id", sa.Integer()))
    sa.Table("users", metadata, sa.Column("id", sa.Integer()))
    columns: list[sa.SchemaItem] = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer()),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=240), nullable=False),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    ]
    if include_discard_fields:
        columns.extend(
            [
                sa.Column("discarded_at", sa.DateTime(timezone=True)),
                sa.Column("discard_reason", sa.Text()),
                sa.Column("email_claimed_at", sa.DateTime(timezone=True)),
            ]
        )
    columns.extend(
        [
            sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
            sa.Column(
                "ok",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("error", sa.Text()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["request_id"],
                ["box_requests.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["recipient_user_id"],
                ["users.id"],
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint("idempotency_key"),
        ]
    )
    if include_discard_fields:
        columns.append(
            sa.CheckConstraint(
                "(discarded_at IS NULL AND discard_reason IS NULL) OR "
                "(discarded_at IS NOT NULL AND discard_reason IS NOT NULL)",
                name=_OUTBOX_DISCARD_CHECK,
            )
        )
    table = sa.Table("request_email_outbox", metadata, *columns)
    sa.Index(
        "ix_request_email_outbox_pending",
        table.c.sent_at,
        table.c.available_at,
    )
    sa.Index(
        "ix_request_email_outbox_request",
        table.c.request_id,
        table.c.created_at,
    )
    return table


def _create_deployment_timestamp() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            f"CREATE TEMPORARY TABLE {_DEPLOYMENT_TIMESTAMP_TABLE} "
            "(deployed_at TIMESTAMPTZ NOT NULL) ON COMMIT DROP"
        )
        op.execute(
            f"INSERT INTO {_DEPLOYMENT_TIMESTAMP_TABLE} (deployed_at) "
            "VALUES (transaction_timestamp())"
        )
        return
    op.execute(
        f"CREATE TEMPORARY TABLE {_DEPLOYMENT_TIMESTAMP_TABLE} "
        "(deployed_at DATETIME NOT NULL)"
    )
    op.execute(
        f"INSERT INTO {_DEPLOYMENT_TIMESTAMP_TABLE} (deployed_at) "
        "VALUES (CURRENT_TIMESTAMP)"
    )


def _add_outbox_discard_schema() -> None:
    context = op.get_context()
    discarded_at = sa.Column(
        "discarded_at",
        sa.DateTime(timezone=True),
        nullable=True,
    )
    discard_reason = sa.Column("discard_reason", sa.Text(), nullable=True)
    email_claimed_at = sa.Column(
        "email_claimed_at",
        sa.DateTime(timezone=True),
        nullable=True,
    )
    if context.dialect.name == "sqlite":
        with op.batch_alter_table(
            "request_email_outbox",
            copy_from=_request_email_outbox_table(
                include_discard_fields=False,
            ),
            recreate="always",
        ) as batch_op:
            batch_op.add_column(discarded_at)
            batch_op.add_column(discard_reason)
            batch_op.add_column(email_claimed_at)
            batch_op.create_check_constraint(
                _OUTBOX_DISCARD_CHECK,
                "(discarded_at IS NULL AND discard_reason IS NULL) OR "
                "(discarded_at IS NOT NULL AND discard_reason IS NOT NULL)",
            )
        return
    op.add_column("request_email_outbox", discarded_at)
    op.add_column("request_email_outbox", discard_reason)
    op.add_column("request_email_outbox", email_claimed_at)
    op.create_check_constraint(
        _OUTBOX_DISCARD_CHECK,
        "request_email_outbox",
        "(discarded_at IS NULL AND discard_reason IS NULL) OR "
        "(discarded_at IS NOT NULL AND discard_reason IS NOT NULL)",
    )


def _consolidate_and_resolve_alerts() -> None:
    context = op.get_context()
    on_commit = " ON COMMIT DROP" if context.dialect.name == "postgresql" else ""
    op.execute(
        f"""
        CREATE TEMPORARY TABLE {_ALERT_DUPLICATE_MAP_TABLE} (
            duplicate_id INTEGER PRIMARY KEY,
            canonical_id INTEGER NOT NULL
        ){on_commit}
        """
    )
    op.execute(
        f"""
        INSERT INTO {_ALERT_DUPLICATE_MAP_TABLE} (
            duplicate_id,
            canonical_id
        )
        SELECT id, canonical_id
        FROM (
            SELECT
                id,
                FIRST_VALUE(id) OVER (
                    PARTITION BY warehouse_id, type
                    ORDER BY triggered_at, id
                ) AS canonical_id,
                ROW_NUMBER() OVER (
                    PARTITION BY warehouse_id, type
                    ORDER BY triggered_at, id
                ) AS open_rank
            FROM alerts
            WHERE resolved_at IS NULL
              AND type <> 'box_stuck'
        ) AS ranked_open_alerts
        WHERE open_rank > 1
        """
    )

    # Preserve every audit row while consolidating delivery state onto the
    # canonical earliest incident. This must happen before duplicates are
    # resolved so the canonical row inherits aggregate attempts and successes.
    op.execute(
        f"""
        UPDATE alert_notifications
        SET alert_id = (
            SELECT canonical_id
            FROM {_ALERT_DUPLICATE_MAP_TABLE}
            WHERE duplicate_id = alert_notifications.alert_id
        )
        WHERE alert_id IN (
            SELECT duplicate_id FROM {_ALERT_DUPLICATE_MAP_TABLE}
        )
        """
    )
    op.execute(
        f"""
        UPDATE alerts
        SET notified_at = COALESCE(
            (
                SELECT MIN(alert_notifications.sent_at)
                FROM alert_notifications
                WHERE alert_notifications.alert_id = alerts.id
                  AND alert_notifications.kind = 'triggered'
                  AND alert_notifications.ok = TRUE
            ),
            (
                SELECT MIN(candidate_alert.notified_at)
                FROM alerts AS candidate_alert
                WHERE candidate_alert.notified_at IS NOT NULL
                  AND (
                      candidate_alert.id = alerts.id
                      OR candidate_alert.id IN (
                          SELECT duplicate_id
                          FROM {_ALERT_DUPLICATE_MAP_TABLE}
                          WHERE canonical_id = alerts.id
                      )
                  )
            )
        )
        WHERE id IN (
            SELECT canonical_id FROM {_ALERT_DUPLICATE_MAP_TABLE}
        )
        """
    )
    op.execute(
        f"""
        UPDATE alerts
        SET resolved_at = (
            SELECT deployed_at FROM {_DEPLOYMENT_TIMESTAMP_TABLE}
        )
        WHERE resolved_at IS NULL
          AND (
              type = 'box_stuck'
              OR id IN (
                  SELECT duplicate_id FROM {_ALERT_DUPLICATE_MAP_TABLE}
              )
          )
        """
    )
    op.execute(f"DROP TABLE {_ALERT_DUPLICATE_MAP_TABLE}")


def _discard_suppressed_request_email() -> None:
    allowed_kinds = ", ".join(
        f"'{kind}'" for kind in _ALLOWED_REQUEST_EMAIL_KINDS
    )
    op.execute(
        f"""
        UPDATE request_email_outbox
        SET discarded_at = (
                SELECT deployed_at FROM {_DEPLOYMENT_TIMESTAMP_TABLE}
            ),
            discard_reason = 'notification_policy_reduced'
        WHERE sent_at IS NULL
          AND kind NOT IN ({allowed_kinds})
        """
    )


def upgrade() -> None:
    _create_deployment_timestamp()
    _add_outbox_discard_schema()
    op.add_column(
        "alerts",
        sa.Column(
            "email_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    _consolidate_and_resolve_alerts()
    _discard_suppressed_request_email()
    op.create_index(
        _ALERT_OPEN_INDEX,
        "alerts",
        ["warehouse_id", "type"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )
    op.create_index(
        _OUTBOX_DISPATCH_INDEX,
        "request_email_outbox",
        ["available_at"],
        postgresql_where=sa.text(
            "sent_at IS NULL AND discarded_at IS NULL"
        ),
        sqlite_where=sa.text("sent_at IS NULL AND discarded_at IS NULL"),
    )
    op.execute(f"DROP TABLE {_DEPLOYMENT_TIMESTAMP_TABLE}")


def _drop_outbox_discard_schema() -> None:
    context = op.get_context()
    if context.dialect.name == "sqlite":
        with op.batch_alter_table(
            "request_email_outbox",
            copy_from=_request_email_outbox_table(
                include_discard_fields=True,
            ),
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                _OUTBOX_DISCARD_CHECK,
                type_="check",
            )
            batch_op.drop_column("email_claimed_at")
            batch_op.drop_column("discard_reason")
            batch_op.drop_column("discarded_at")
        return
    op.drop_constraint(
        _OUTBOX_DISCARD_CHECK,
        "request_email_outbox",
        type_="check",
    )
    op.drop_column("request_email_outbox", "email_claimed_at")
    op.drop_column("request_email_outbox", "discard_reason")
    op.drop_column("request_email_outbox", "discarded_at")


def downgrade() -> None:
    # Deliberately schema-only: resolved alerts stay resolved and discarded
    # delivery intent cannot be reconstructed after its audit columns go away.
    op.drop_index(
        _OUTBOX_DISPATCH_INDEX,
        table_name="request_email_outbox",
    )
    op.drop_index(_ALERT_OPEN_INDEX, table_name="alerts")
    op.drop_column("alerts", "email_claimed_at")
    _drop_outbox_discard_schema()
