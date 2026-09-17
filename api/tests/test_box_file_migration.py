from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0035_first_class_box_files.py"
    )
    spec = importlib.util.spec_from_file_location(
        "migration_0035_first_class_box_files",
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
    lots = sa.Table(
        "lots",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    boxes = sa.Table(
        "boxes",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "lot_id",
            sa.Integer(),
            sa.ForeignKey("lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("box_number", sa.String(64), nullable=False),
        sa.Column("contents", sa.Text()),
    )
    request_items = sa.Table(
        "box_request_items",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contents", sa.Text()),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(lots.insert(), {"id": 1})
        connection.execute(
            boxes.insert(),
            {
                "id": 7,
                "lot_id": 1,
                "box_number": "  A  01 ",
                "contents": " first | | second ",
            },
        )
        connection.execute(
            request_items.insert(),
            {"id": 9, "contents": " requested one | requested two "},
        )
    return engine


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def test_sqlite_upgrade_backfills_constraints_and_safe_downgrade() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("boxes")
        } >= {"uq_boxes_id_lot_id"}
        file_foreign_keys = inspector.get_foreign_keys("box_files")
        assert any(
            foreign_key["constrained_columns"] == ["box_id", "lot_id"]
            and foreign_key["referred_columns"] == ["id", "lot_id"]
            for foreign_key in file_foreign_keys
        )
        assert connection.execute(
            sa.text(
                """
                SELECT reference, description, position
                FROM box_files
                ORDER BY position
                """
            )
        ).all() == [
            ("LEGACY-A 01-BOX-7-FILE-1", "first", 1),
            ("LEGACY-A 01-BOX-7-FILE-2", "second", 2),
        ]
        assert connection.execute(
            sa.text(
                """
                SELECT reference, description, position, snapshot_kind
                FROM box_request_item_file_snapshots
                ORDER BY position
                """
            )
        ).all() == [
            ("LEGACY-REQUEST-ITEM-9-FILE-1", "requested one", 1, "legacy_contents"),
            ("LEGACY-REQUEST-ITEM-9-FILE-2", "requested two", 2, "legacy_contents"),
        ]

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "box_files" not in inspector.get_table_names()
        assert "box_request_item_file_snapshots" not in inspector.get_table_names()
        assert "uq_boxes_id_lot_id" not in {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("boxes")
        }
    engine.dispose()


def test_sqlite_downgrade_rejects_changed_first_class_data() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()
        connection.execute(
            sa.text("UPDATE box_files SET description = 'changed' WHERE position = 1")
        )

        with pytest.raises(RuntimeError, match="downgrade blocked"):
            migration.downgrade()
        assert "box_files" in sa.inspect(connection).get_table_names()
    engine.dispose()
