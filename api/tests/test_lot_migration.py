from __future__ import annotations

import importlib.util
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
    assert ScriptDirectory.from_config(config).get_heads() == ["0028_safe_lot_merge"]
    migration = _load_merge_migration()
    assert migration.down_revision == "0027_first_class_lots"
