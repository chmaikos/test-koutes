from __future__ import annotations

import importlib.util
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.models.requests import BoxRequest, BoxRequestDirection, BoxRequestStatus
from app.models.users import UserRole
from app.schemas.requests import BoxRequestCreate
from app.services.request_reporting import (
    RequestReportFilters,
    build_reconciliation,
)


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0031_return_target_warehouse.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0031_return_target_warehouse",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def _legacy_database() -> sa.Engine:
    engine = sa.create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = sa.MetaData()
    warehouses = sa.Table(
        "warehouses",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    requests = sa.Table(
        "box_requests",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column(
            "warehouse_id",
            sa.Integer(),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(warehouses.insert(), [{"id": 1}, {"id": 2}])
        connection.execute(
            requests.insert(),
            [
                {"id": 1, "direction": "inbound", "warehouse_id": 1},
                {"id": 2, "direction": "return", "warehouse_id": 1},
            ],
        )
    return engine


def test_create_schema_enforces_direction_aware_target_contract() -> None:
    same_source = BoxRequestCreate(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        target_warehouse_id=1,
        quantity=1,
    )
    assert same_source.target_warehouse_id == 1

    with pytest.raises(ValidationError, match="target_warehouse_id is required"):
        BoxRequestCreate(
            direction=BoxRequestDirection.return_,
            warehouse_id=1,
            quantity=1,
        )
    with pytest.raises(ValidationError, match="inbound requests cannot specify"):
        BoxRequestCreate(
            direction=BoxRequestDirection.inbound,
            warehouse_id=1,
            target_warehouse_id=2,
            quantity=1,
        )


def test_model_requires_explicit_return_target_and_enforces_check(session) -> None:
    inbound = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
    )
    session.add(inbound)
    session.commit()
    assert inbound.target_warehouse_id is None

    missing_target = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
    )
    session.add(missing_target)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    explicit_target = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        target_warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
    )
    session.add(explicit_target)
    session.commit()
    assert explicit_target.target_warehouse_id == 1

    inbound.target_warehouse_id = 2
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_reconciliation_reports_return_target_warehouse(session, make_user) -> None:
    user = make_user(UserRole.operator)
    now = datetime.now(UTC)
    request = BoxRequest(
        direction=BoxRequestDirection.return_,
        warehouse_id=1,
        target_warehouse_id=2,
        quantity=1,
        status=BoxRequestStatus.in_transit,
        requester_user_id=user.id,
        sla_deadline=now - timedelta(hours=1),
        submitted_at=now - timedelta(hours=2),
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
    )
    session.add(request)
    session.commit()

    report = build_reconciliation(
        session,
        user=user,
        filters=RequestReportFilters(warehouse_id=1),
        now=now,
    )

    issues = [issue for issue in report.items if issue.request_id == request.id]
    assert issues
    assert {
        (issue.target_warehouse_id, issue.target_warehouse_name)
        for issue in issues
    } == {(2, "Building 2")}


def test_sqlite_migration_backfills_contract_and_downgrades_safely() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("box_requests")
        }
        assert columns["target_warehouse_id"]["nullable"] is True
        assert {
            index["name"] for index in inspector.get_indexes("box_requests")
        } >= {"ix_box_requests_target_warehouse_id"}
        target_fk = next(
            foreign_key
            for foreign_key in inspector.get_foreign_keys("box_requests")
            if foreign_key["constrained_columns"] == ["target_warehouse_id"]
        )
        assert target_fk["options"]["ondelete"] == "RESTRICT"
        assert {
            check["name"] for check in inspector.get_check_constraints("box_requests")
        } >= {"ck_box_requests_direction_target_warehouse"}
        assert connection.execute(
            sa.text(
                "SELECT id, target_warehouse_id FROM box_requests ORDER BY id"
            )
        ).all() == [(1, None), (2, 1)]

        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO box_requests "
                    "(id, direction, warehouse_id, target_warehouse_id) "
                    "VALUES (3, 'inbound', 1, 2)"
                )
            )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO box_requests "
                    "(id, direction, warehouse_id, target_warehouse_id) "
                    "VALUES (4, 'return', 1, NULL)"
                )
            )

        migration.downgrade()
        assert "target_warehouse_id" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("box_requests")
        }
    engine.dispose()


def test_sqlite_migration_blocks_lossy_cross_warehouse_downgrade() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()
        connection.execute(
            sa.text(
                "UPDATE box_requests SET target_warehouse_id = 2 WHERE id = 2"
            )
        )
        with pytest.raises(RuntimeError, match="cross-warehouse return targets exist"):
            migration.downgrade()
    engine.dispose()


def test_postgresql_offline_migration_sql_is_complete_and_guarded() -> None:
    migration = _load_migration()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(context)

    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()

    assert "ADD COLUMN target_warehouse_id INTEGER" in sql
    assert "SET target_warehouse_id = warehouse_id" in sql
    assert "FOREIGN KEY(target_warehouse_id) REFERENCES warehouses" in sql
    assert "ck_box_requests_direction_target_warehouse" in sql
    assert "CREATE INDEX ix_box_requests_target_warehouse_id" in sql
    assert "cross-warehouse return targets exist" in sql
    assert "DROP COLUMN target_warehouse_id" in sql


def test_target_migration_follows_0030() -> None:
    migration = _load_migration()
    assert migration.revision == "0031_return_target_warehouse"
    assert migration.down_revision == "0030_force_purge_adjustment"
