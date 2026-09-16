from __future__ import annotations

import importlib.util
import io
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from app.models.alerts import Alert, AlertNotificationKind, AlertType
from app.models.notifications import RequestEmailOutbox


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0034_reduced_email_notifications.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0034_reduced_email_notifications",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://", future=True)
    metadata = sa.MetaData()
    warehouses = sa.Table(
        "warehouses",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    requests = sa.Table(
        "box_requests",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    alerts = sa.Table(
        "alerts",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column(
            "acknowledged_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("escalated_at", sa.DateTime(timezone=True)),
    )
    alert_notifications = sa.Table(
        "alert_notifications",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_id",
            sa.Integer(),
            sa.ForeignKey("alerts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipients", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    outbox = sa.Table(
        "request_email_outbox",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False, unique=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
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
    )
    sa.Index(
        "ix_alert_notifications_alert_sent",
        alert_notifications.c.alert_id,
        alert_notifications.c.sent_at,
    )
    sa.Index(
        "ix_request_email_outbox_pending",
        outbox.c.sent_at,
        outbox.c.available_at,
    )
    sa.Index(
        "ix_request_email_outbox_request",
        outbox.c.request_id,
        outbox.c.created_at,
    )
    metadata.create_all(engine)
    old_resolution = datetime(2025, 1, 1, tzinfo=UTC)
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = datetime(2026, 1, 2, tzinfo=UTC)
    sent = datetime(2026, 2, 1, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(warehouses.insert(), [{"id": 1}, {"id": 2}])
        connection.execute(users.insert(), [{"id": 1}])
        connection.execute(requests.insert(), [{"id": 1}])
        connection.execute(
            alerts.insert(),
            [
                {
                    "id": 1,
                    "warehouse_id": 1,
                    "type": "low_inventory",
                    "threshold": 5,
                    "value": 1,
                    "triggered_at": first,
                },
                {
                    "id": 2,
                    "warehouse_id": 1,
                    "type": "low_inventory",
                    "threshold": 5,
                    "value": 2,
                    "triggered_at": second,
                },
                {
                    "id": 3,
                    "warehouse_id": 1,
                    "type": "low_inventory",
                    "threshold": 5,
                    "value": 3,
                    "triggered_at": first,
                    "resolved_at": old_resolution,
                },
                {
                    "id": 4,
                    "warehouse_id": 1,
                    "type": "box_stuck",
                    "threshold": 7,
                    "value": 4,
                    "triggered_at": first,
                },
                {
                    "id": 5,
                    "warehouse_id": 2,
                    "type": "box_stuck",
                    "threshold": 7,
                    "value": 5,
                    "triggered_at": second,
                },
                {
                    "id": 6,
                    "warehouse_id": 2,
                    "type": "max_capacity",
                    "threshold": 100,
                    "value": 101,
                    "triggered_at": first,
                },
                {
                    "id": 7,
                    "warehouse_id": 2,
                    "type": "max_capacity",
                    "threshold": 100,
                    "value": 102,
                    "triggered_at": first,
                },
            ],
        )
        connection.execute(
            alerts.update()
            .where(alerts.c.id == 3)
            .values(resolved_at=old_resolution)
        )
        connection.execute(
            alerts.update().where(alerts.c.id == 7).values(notified_at=sent)
        )
        connection.execute(
            alert_notifications.insert(),
            (
                [
                    {
                        "id": index,
                        "alert_id": 4,
                        "kind": kind,
                        "sent_at": sent,
                        "recipients": "ops@example.com",
                        "ok": True,
                    }
                    for index, kind in enumerate(
                        ("triggered", "reminder", "escalated", "resolved"),
                        start=1,
                    )
                ]
                + [
                    {
                        "id": 5,
                        "alert_id": 2,
                        "kind": "triggered",
                        "sent_at": sent,
                        "recipients": "ops@example.com",
                        "ok": True,
                    }
                ]
                + [
                    {
                        "id": index,
                        "alert_id": 6 if index < 8 else 7,
                        "kind": "triggered",
                        "sent_at": sent,
                        "recipients": "ops@example.com",
                        "ok": False,
                        "error": "failed",
                    }
                    for index in range(6, 11)
                ]
            ),
        )
        kinds = [
            "rejected",
            "rejected",
            "staged",
            "submitted",
            "approved",
            "confirmation_received",
            "overdue",
        ]
        connection.execute(
            outbox.insert(),
            [
                {
                    "id": index,
                    "request_id": 1,
                    "recipient_user_id": 1,
                    "recipient_email": "ops@example.com",
                    "kind": kind,
                    "subject": kind,
                    "html_body": kind,
                    "text_body": kind,
                    "idempotency_key": f"email-{index}",
                    "attempts": 2 if index in {4, 7} else 0,
                    "sent_at": sent if index == 2 else None,
                    "last_attempt_at": sent if index in {4, 7} else None,
                    "error": "retry" if index in {4, 7} else None,
                }
                for index, kind in enumerate(kinds, start=1)
            ],
        )
    return engine


def test_models_expose_discard_audit_and_partial_indexes() -> None:
    assert {member.value for member in AlertType} == {
        "low_inventory",
        "max_capacity",
        "near_capacity",
        "near_low_inventory",
        "box_stuck",
    }
    assert {member.value for member in AlertNotificationKind} == {
        "triggered",
        "reminder",
        "escalated",
        "resolved",
        "test",
    }
    alert_index = next(
        index
        for index in Alert.__table__.indexes
        if index.name == "uq_alerts_open_warehouse_type"
    )
    assert alert_index.unique is True
    assert str(alert_index.dialect_options["postgresql"]["where"]) == (
        "resolved_at IS NULL"
    )
    assert str(alert_index.dialect_options["sqlite"]["where"]) == (
        "resolved_at IS NULL"
    )
    assert Alert.__table__.c.email_claimed_at.nullable is True
    assert Alert.__table__.c.email_claimed_at.type.timezone is True

    columns = RequestEmailOutbox.__table__.c
    assert columns.discarded_at.nullable is True
    assert columns.discarded_at.type.timezone is True
    assert columns.discard_reason.nullable is True
    assert columns.email_claimed_at.nullable is True
    assert columns.email_claimed_at.type.timezone is True
    assert any(
        constraint.name == "ck_request_email_outbox_discard_consistency"
        for constraint in RequestEmailOutbox.__table__.constraints
    )
    dispatch_index = next(
        index
        for index in RequestEmailOutbox.__table__.indexes
        if index.name == "ix_request_email_outbox_dispatch_pending"
    )
    predicate = "sent_at IS NULL AND discarded_at IS NULL"
    assert str(dispatch_index.dialect_options["postgresql"]["where"]) == predicate
    assert str(dispatch_index.dialect_options["sqlite"]["where"]) == predicate


def test_sqlite_upgrade_transforms_data_enforces_indexes_and_downgrades() -> None:
    migration = _load_migration()
    engine = _legacy_engine()
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("request_email_outbox")
        }
        assert {"discarded_at", "discard_reason", "email_claimed_at"} <= columns.keys()
        alert_columns = {
            column["name"] for column in inspector.get_columns("alerts")
        }
        assert "email_claimed_at" in alert_columns
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "request_email_outbox"
            )
        } == {"ck_request_email_outbox_discard_consistency"}
        alert_indexes = {
            index["name"]: index for index in inspector.get_indexes("alerts")
        }
        assert alert_indexes["uq_alerts_open_warehouse_type"]["unique"] == 1
        assert (
            str(
                alert_indexes["uq_alerts_open_warehouse_type"][
                    "dialect_options"
                ]["sqlite_where"]
            )
            == "resolved_at IS NULL"
        )
        outbox_indexes = {
            index["name"]: index
            for index in inspector.get_indexes("request_email_outbox")
        }
        assert {
            "ix_request_email_outbox_dispatch_pending",
            "ix_request_email_outbox_pending",
            "ix_request_email_outbox_request",
        } <= outbox_indexes.keys()

        alert_rows = connection.execute(
            sa.text("SELECT id, resolved_at FROM alerts ORDER BY id")
        ).all()
        by_alert_id = dict(alert_rows)
        assert by_alert_id[1] is None
        assert by_alert_id[6] is None
        assert by_alert_id[3].startswith("2025-01-01")
        deployment_timestamp = by_alert_id[2]
        assert deployment_timestamp is not None
        assert {by_alert_id[index] for index in (2, 4, 5, 7)} == {
            deployment_timestamp
        }
        notified_rows = dict(
            connection.execute(
                sa.text("SELECT id, notified_at FROM alerts ORDER BY id")
            ).all()
        )
        assert notified_rows[1].startswith("2026-02-01")
        assert notified_rows[6].startswith("2026-02-01")

        notification_rows = connection.execute(
            sa.text(
                "SELECT id, alert_id, kind, ok "
                "FROM alert_notifications ORDER BY id"
            )
        ).all()
        assert notification_rows[:4] == [
            (1, 4, "triggered", 1),
            (2, 4, "reminder", 1),
            (3, 4, "escalated", 1),
            (4, 4, "resolved", 1),
        ]
        assert notification_rows[4] == (5, 1, "triggered", 1)
        assert notification_rows[5:] == [
            (index, 6, "triggered", 0) for index in range(6, 11)
        ]
        assert connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM alert_notifications "
                "WHERE alert_id = 6 AND kind = 'triggered'"
            )
        ) == 5

        outbox_rows = connection.execute(
            sa.text(
                "SELECT id, discarded_at, discard_reason "
                "FROM request_email_outbox ORDER BY id"
            )
        ).all()
        by_outbox_id = {
            row.id: (row.discarded_at, row.discard_reason)
            for row in outbox_rows
        }
        assert by_outbox_id[1] == (
            deployment_timestamp,
            "notification_policy_reduced",
        )
        assert by_outbox_id[7] == (
            deployment_timestamp,
            "notification_policy_reduced",
        )
        assert all(
            by_outbox_id[index] == (None, None)
            for index in (2, 3, 4, 5, 6)
        )

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO alerts "
                    "(id, warehouse_id, type, threshold, value, triggered_at) "
                    "VALUES (10, 1, 'low_inventory', 5, 0, CURRENT_TIMESTAMP)"
                )
            )
        connection.execute(
            sa.text(
                "INSERT INTO alerts "
                "(id, warehouse_id, type, threshold, value, triggered_at, resolved_at) "
                "VALUES (11, 1, 'low_inventory', 5, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "UPDATE request_email_outbox "
                    "SET discard_reason = 'inconsistent' WHERE id = 3"
                )
            )

        migration.downgrade()
        downgraded = sa.inspect(connection)
        downgraded_columns = {
            column["name"]
            for column in downgraded.get_columns("request_email_outbox")
        }
        assert "discarded_at" not in downgraded_columns
        assert "discard_reason" not in downgraded_columns
        assert "email_claimed_at" not in downgraded_columns
        assert "email_claimed_at" not in {
            column["name"] for column in downgraded.get_columns("alerts")
        }
        assert not downgraded.get_check_constraints("request_email_outbox")
        assert "uq_alerts_open_warehouse_type" not in {
            index["name"] for index in downgraded.get_indexes("alerts")
        }
        assert "ix_request_email_outbox_dispatch_pending" not in {
            index["name"]
            for index in downgraded.get_indexes("request_email_outbox")
        }
        assert connection.scalar(
            sa.text("SELECT resolved_at FROM alerts WHERE id = 4")
        ) == deployment_timestamp
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM request_email_outbox")
        ) == 7
        assert connection.scalar(
            sa.text("SELECT COUNT(*) FROM alert_notifications")
        ) == 10
        connection.execute(
            sa.text(
                "INSERT INTO alerts "
                "(id, warehouse_id, type, threshold, value, triggered_at) "
                "VALUES (12, 1, 'low_inventory', 5, 0, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_offline_sql_and_single_head(dialect: str) -> None:
    migration = _load_migration()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name=dialect,
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(context)
    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()

    assert "0034_deployment_timestamp" in sql
    assert "ROW_NUMBER() OVER" in sql
    assert "FIRST_VALUE(id) OVER" in sql
    assert "_0034_alert_duplicate_map" in sql
    assert "UPDATE alert_notifications" in sql
    assert "notification_policy_reduced" in sql
    assert "uq_alerts_open_warehouse_type" in sql
    assert "resolved_at IS NULL" in sql
    assert "discarded_at" in sql
    assert "discard_reason" in sql
    assert sql.count("email_claimed_at") >= 2
    assert "DROP INDEX" in sql
    assert "ALTER TYPE alert_type" not in sql
    assert "DROP TYPE" not in sql

    root = Path(__file__).parents[1]
    alembic_config = Config(str(root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(root / "alembic"))
    assert ScriptDirectory.from_config(alembic_config).get_heads() == [
        "0034_reduced_email_notifications"
    ]
