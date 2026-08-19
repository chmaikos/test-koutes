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


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0027_first_class_lots.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0027_first_class_lots", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_merge_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0028_safe_lot_merge.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0028_safe_lot_merge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_purge_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0029_lot_purge_audit.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0029_lot_purge_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_force_adjustment_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0030_force_purge_adjustment_event.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0030_force_purge_adjustment_event",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_database() -> sa.Engine:
    engine = sa.create_engine("sqlite://", future=True)
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    boxes = sa.Table(
        "boxes",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("box_number", sa.String(64), nullable=False),
        sa.Column("lot", sa.String(64), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("lot", "box_number", name="uq_boxes_lot_box_number"),
    )
    items = sa.Table(
        "box_request_items",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("box_id", sa.Integer(), sa.ForeignKey("boxes.id", ondelete="RESTRICT")),
        sa.Column("lot", sa.String(64)),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            boxes.insert(),
            [
                {"id": 1, "box_number": "001", "lot": "  Alpha\tLot "},
                {
                    "id": 2,
                    "box_number": "002",
                    "lot": "ALPHA LOT",
                    "archived_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            ],
        )
        connection.execute(
            items.insert(),
            [
                {"id": 1, "box_id": 1, "lot": "Former Lot"},
                {"id": 2, "box_id": None, "lot": "Beta Lot"},
            ],
        )
    return engine


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def test_sqlite_upgrade_backfills_and_downgrade_restores_text() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()

        inspector = sa.inspect(connection)
        box_columns = {column["name"]: column for column in inspector.get_columns("boxes")}
        assert "lot" not in box_columns
        assert box_columns["lot_id"]["nullable"] is False
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("boxes")
        } == {("lot_id", "box_number")}
        box_lot_fk = next(
            fk
            for fk in inspector.get_foreign_keys("boxes")
            if fk["constrained_columns"] == ["lot_id"]
        )
        assert box_lot_fk["options"]["ondelete"] == "RESTRICT"

        lots = connection.execute(
            sa.text("SELECT id, name, normalized_name FROM lots ORDER BY normalized_name")
        ).mappings()
        assert [(row["name"], row["normalized_name"]) for row in lots] == [
            ("Alpha Lot", "alpha lot"),
            ("Beta Lot", "beta lot"),
            ("Former Lot", "former lot"),
        ]
        request_items = connection.execute(
            sa.text(
                """
                SELECT i.lot, l.name
                FROM box_request_items i
                JOIN lots l ON l.id = i.lot_id
                ORDER BY i.id
                """
            )
        ).all()
        assert request_items == [
            ("Former Lot", "Former Lot"),
            ("Beta Lot", "Beta Lot"),
        ]

        migration.downgrade()
        restored = connection.execute(
            sa.text("SELECT lot, box_number FROM boxes ORDER BY id")
        ).all()
        assert restored == [("Alpha Lot", "001"), ("Alpha Lot", "002")]
        assert "lot_id" not in {
            column["name"] for column in sa.inspect(connection).get_columns("boxes")
        }
    engine.dispose()


def test_sqlite_upgrade_reports_normalized_duplicate_box_numbers() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE boxes SET box_number = '001', lot = ' alpha  lot ' WHERE id = 2"
            )
        )
        migration.op = _operations(connection)
        with pytest.raises(RuntimeError, match="duplicate box identities.*alpha lot"):
            migration.upgrade()
    engine.dispose()


def test_migration_shape_follows_0026() -> None:
    migration = _load_migration()
    assert migration.revision == "0027_first_class_lots"
    assert migration.down_revision == "0026_request_workflow_final"


def test_merge_migration_sqlite_upgrade_and_safe_downgrade() -> None:
    first_class = _load_migration()
    merge = _load_merge_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        first_class.op = _operations(connection)
        first_class.upgrade()
        merge.op = _operations(connection)
        merge.upgrade()

        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("lots")
        }
        assert columns["normalized_name"]["nullable"] is True
        assert {"merged_into_lot_id", "merged_at", "merged_by_user_id"} <= columns.keys()
        assert any(
            fk["constrained_columns"] == ["merged_into_lot_id"]
            and fk["options"]["ondelete"] == "RESTRICT"
            for fk in sa.inspect(connection).get_foreign_keys("lots")
        )

        merge.downgrade()
        restored_columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("lots")
        }
        assert restored_columns["normalized_name"]["nullable"] is False
        assert "merged_into_lot_id" not in restored_columns
    engine.dispose()


def test_merge_migration_is_single_head() -> None:
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [
        "0032_first_class_pallets"
    ]
    migration = _load_merge_migration()
    assert migration.down_revision == "0027_first_class_lots"


def test_purge_migration_sqlite_schema_and_safe_downgrade() -> None:
    first_class = _load_migration()
    merge = _load_merge_migration()
    purge = _load_purge_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        first_class.op = _operations(connection)
        first_class.upgrade()
        merge.op = _operations(connection)
        merge.upgrade()
        purge.op = _operations(connection)
        purge.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("lot_purge_events")
        }
        assert {
            "lot_id",
            "lot_name",
            "lot_version",
            "actor_user_id",
            "reason",
            "receipt_ids",
            "receipt_count",
            "archived_box_ids",
            "archived_box_count",
            "object_keys",
            "object_key_count",
            "object_cleanup_status",
            "object_cleanup_failures",
            "object_cleanup_completed_at",
            "created_at",
            "updated_at",
            "metadata",
        } <= columns.keys()
        foreign_keys = inspector.get_foreign_keys("lot_purge_events")
        assert not any(fk["referred_table"] == "lots" for fk in foreign_keys)
        actor_fk = next(
            fk
            for fk in foreign_keys
            if fk["constrained_columns"] == ["actor_user_id"]
        )
        assert actor_fk["options"]["ondelete"] == "SET NULL"
        assert {
            index["name"] for index in inspector.get_indexes("lot_purge_events")
        } >= {
            "ix_lot_purge_events_lot_created",
            "ix_lot_purge_events_actor_created",
            "ix_lot_purge_events_cleanup_updated",
        }

        connection.execute(
            sa.text(
                """
                INSERT INTO lot_purge_events
                    (lot_id, lot_name, lot_version, reason)
                VALUES
                    (42, 'Deleted lot', 3, 'Confirmed duplicate receipt')
                """
            )
        )
        with pytest.raises(RuntimeError, match="lot purge audit data exists"):
            purge.downgrade()
        connection.execute(sa.text("DELETE FROM lot_purge_events"))
        purge.downgrade()
        assert "lot_purge_events" not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_purge_migration_offline_postgresql_sql_is_valid_and_safe() -> None:
    migration = _load_purge_migration()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(context)

    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()

    assert "CREATE TABLE lot_purge_events" in sql
    assert "FOREIGN KEY(actor_user_id) REFERENCES users" in sql
    assert "FOREIGN KEY(lot_id) REFERENCES lots" not in sql
    assert "IF EXISTS (SELECT 1 FROM lot_purge_events)" in sql
    assert "RAISE EXCEPTION" in sql
    assert "DROP TABLE lot_purge_events" in sql


def test_purge_migration_follows_merge_revision() -> None:
    migration = _load_purge_migration()
    assert migration.revision == "0029_lot_purge_audit"
    assert migration.down_revision == "0028_safe_lot_merge"


def test_force_adjustment_migration_sqlite_is_compatible_and_guarded() -> None:
    migration = _load_force_adjustment_migration()
    engine = sa.create_engine("sqlite://", future=True)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                CREATE TABLE box_request_events (
                    id INTEGER PRIMARY KEY,
                    event_type VARCHAR(64) NOT NULL
                )
                """
            )
        )
        migration.op = _operations(connection)
        migration.upgrade()
        migration.downgrade()

        connection.execute(
            sa.text(
                """
                INSERT INTO box_request_events (id, event_type)
                VALUES (1, 'force_purge_adjusted')
                """
            )
        )
        with pytest.raises(
            RuntimeError,
            match="force purge adjustment events exist",
        ):
            migration.downgrade()
    engine.dispose()


def test_force_adjustment_migration_offline_postgresql_sql_is_safe() -> None:
    migration = _load_force_adjustment_migration()
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    migration.op = Operations(context)

    migration.upgrade()
    migration.downgrade()
    sql = output.getvalue()

    assert (
        "ALTER TYPE box_request_event_type "
        "ADD VALUE IF NOT EXISTS 'force_purge_adjusted'"
    ) in sql
    assert "WHERE event_type = 'force_purge_adjusted'" in sql
    assert "RAISE EXCEPTION" in sql


def test_force_adjustment_migration_follows_purge_revision() -> None:
    migration = _load_force_adjustment_migration()
    assert migration.revision == "0030_force_purge_adjustment"
    assert migration.down_revision == "0029_lot_purge_audit"
