from __future__ import annotations

import importlib.util
import io
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.models import (
    Box,
    BoxFile,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
    BoxRequestItemFileSnapshot,
    BoxRequestItemFileSnapshotKind,
    BoxRequestOrigin,
    BoxRequestStatus,
    BoxStatus,
    Lot,
)
from scripts.box_files_preflight import analyze_rows


def _load_migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0035_first_class_box_files.py"
    spec = importlib.util.spec_from_file_location("migration_0035", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def _lot_and_box(session, *, lot_name: str, box_number: str) -> tuple[Lot, Box]:
    lot = Lot(name=lot_name)
    session.add(lot)
    session.flush()
    box = Box(
        lot_id=lot.id,
        box_number=box_number,
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    session.add(box)
    session.flush()
    return lot, box


def test_box_file_reference_is_normalized_and_unique_within_lot(session) -> None:
    lot, first_box = _lot_and_box(
        session,
        lot_name="File uniqueness",
        box_number="001",
    )
    second_box = Box(
        lot_id=lot.id,
        box_number="002",
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    session.add(second_box)
    session.flush()
    first = BoxFile(
        lot_id=lot.id,
        box_id=first_box.id,
        reference="  Client   File  ",
        position=1,
    )
    session.add(first)
    session.flush()
    assert first.reference == "Client File"
    assert first.normalized_reference == "client file"

    session.add(
        BoxFile(
            lot_id=lot.id,
            box_id=second_box.id,
            reference="client file",
            position=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_box_file_composite_fk_rejects_cross_lot_placement(session) -> None:
    first_lot, first_box = _lot_and_box(
        session,
        lot_name="First file lot",
        box_number="001",
    )
    second_lot = Lot(name="Second file lot")
    session.add(second_lot)
    session.flush()
    session.add(
        BoxFile(
            lot_id=second_lot.id,
            box_id=first_box.id,
            reference="WRONG-LOT",
            position=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    assert first_lot.id != second_lot.id


def test_request_file_snapshot_fields_are_immutable() -> None:
    snapshot = BoxRequestItemFileSnapshot(
        request_item_id=1,
        file_id=None,
        reference="FILE-1",
        description="Original",
        barcode=None,
        position=1,
        snapshot_kind=BoxRequestItemFileSnapshotKind.tracked_file,
    )
    with pytest.raises(ValueError, match="description snapshot is immutable"):
        snapshot.description = "Changed"


def test_deleting_live_file_preserves_request_snapshot(session) -> None:
    lot, box = _lot_and_box(
        session,
        lot_name="Snapshot retention",
        box_number="001",
    )
    file_record = BoxFile(
        lot_id=lot.id,
        box_id=box.id,
        reference="FILE-1",
        description="Original description",
        position=1,
    )
    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
        origin=BoxRequestOrigin.manual_entry,
    )
    session.add_all([file_record, request])
    session.flush()
    item = BoxRequestItem(
        request_id=request.id,
        position=1,
        box_id=box.id,
        lot_id=lot.id,
        lot=lot.name,
        box_number=box.box_number,
    )
    session.add(item)
    session.flush()
    snapshot = BoxRequestItemFileSnapshot(
        request_item_id=item.id,
        file_id=file_record.id,
        reference=file_record.reference,
        description=file_record.description,
        barcode=file_record.barcode,
        position=file_record.position,
        snapshot_kind=BoxRequestItemFileSnapshotKind.tracked_file,
    )
    session.add(snapshot)
    session.commit()

    session.delete(file_record)
    session.commit()
    session.refresh(snapshot)
    assert snapshot.file_id is None
    assert snapshot.reference == "FILE-1"
    assert snapshot.description == "Original description"


def _legacy_database() -> sa.Engine:
    engine = sa.create_engine("sqlite://", future=True)
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    lots = sa.Table(
        "lots",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    boxes = sa.Table(
        "boxes",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer(), sa.ForeignKey("lots.id"), nullable=False),
        sa.Column("box_number", sa.String(64), nullable=False),
        sa.Column("contents", sa.Text()),
        sa.UniqueConstraint(
            "lot_id",
            "box_number",
            name="uq_boxes_lot_box_number",
        ),
    )
    requests = sa.Table(
        "box_requests",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    items = sa.Table(
        "box_request_items",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("box_requests.id"),
            nullable=False,
        ),
        sa.Column("contents", sa.Text()),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(users.insert(), [{"id": 1}])
        connection.execute(lots.insert(), [{"id": 10}])
        connection.execute(requests.insert(), [{"id": 20}])
        connection.execute(
            boxes.insert(),
            [
                {
                    "id": 30,
                    "lot_id": 10,
                    "box_number": " 001 ",
                    "contents": " Alpha | | Beta  |",
                },
                {
                    "id": 31,
                    "lot_id": 10,
                    "box_number": "002",
                    "contents": "  ",
                },
            ],
        )
        connection.execute(
            items.insert(),
            [
                {
                    "id": 40,
                    "request_id": 20,
                    "contents": " Historical A | Historical B ",
                }
            ],
        )
    return engine


def test_migration_sqlite_backfills_and_safely_downgrades() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        migration.op = _operations(connection)
        migration.upgrade()

        assert migration.split_legacy_contents(" A || B | ") == ["A", "B"]
        files = connection.execute(
            sa.text(
                """
                SELECT
                    lot_id,
                    box_id,
                    reference,
                    normalized_reference,
                    description,
                    position
                FROM box_files
                ORDER BY position
                """
            )
        ).all()
        assert files == [
            (10, 30, "LEGACY-001-BOX-30-FILE-1", "legacy-001-box-30-file-1", "Alpha", 1),
            (10, 30, "LEGACY-001-BOX-30-FILE-2", "legacy-001-box-30-file-2", "Beta", 2),
        ]
        snapshots = connection.execute(
            sa.text(
                """
                SELECT
                    request_item_id,
                    file_id,
                    reference,
                    description,
                    position,
                    snapshot_kind
                FROM box_request_item_file_snapshots
                ORDER BY position
                """
            )
        ).all()
        assert snapshots == [
            (
                40,
                None,
                "LEGACY-REQUEST-ITEM-40-FILE-1",
                "Historical A",
                1,
                "legacy_contents",
            ),
            (
                40,
                None,
                "LEGACY-REQUEST-ITEM-40-FILE-2",
                "Historical B",
                2,
                "legacy_contents",
            ),
        ]
        assert "contents" in {
            column["name"] for column in sa.inspect(connection).get_columns("boxes")
        }

        migration.downgrade()
        assert "box_files" not in sa.inspect(connection).get_table_names()
        assert {
            tuple(constraint["column_names"])
            for constraint in sa.inspect(connection).get_unique_constraints("boxes")
        } == {("lot_id", "box_number")}
    engine.dispose()


def test_migration_downgrade_blocks_changed_first_class_data() -> None:
    migration = _load_migration()
    engine = _legacy_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()
        connection.execute(
            sa.text("UPDATE box_files SET description = 'Edited after migration' WHERE id = 1")
        )
        with pytest.raises(RuntimeError, match="no longer exactly represented"):
            migration.downgrade()
    engine.dispose()


def test_migration_postgresql_offline_sql_uses_portable_check_enums() -> None:
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

    assert migration.down_revision == "0034_reduced_email_notifications"
    assert "CREATE TYPE box_file_event_type" not in sql
    assert "CREATE TABLE box_files" in sql
    assert "string_to_table" in sql
    assert "ON DELETE SET NULL" in sql
    assert "RAISE EXCEPTION" in sql


def test_preflight_reports_duplicates_cross_lot_and_snapshot_gaps() -> None:
    report = analyze_rows(
        [
            {"id": 1, "lot_id": 10, "contents": "A"},
            {"id": 2, "lot_id": 20, "contents": None},
        ],
        [
            {
                "id": 11,
                "lot_id": 10,
                "box_id": 1,
                "reference": " Ref ",
                "normalized_reference": "ref",
            },
            {
                "id": 12,
                "lot_id": 10,
                "box_id": 2,
                "reference": "REF",
                "normalized_reference": "ref",
            },
            {
                "id": 13,
                "lot_id": 10,
                "box_id": 1,
                "reference": " ",
                "normalized_reference": "",
            },
        ],
        [{"id": 21, "contents": "One | Two"}],
        [
            {
                "id": 31,
                "request_item_id": 21,
                "reference": "SNAP-1",
                "description": "One",
                "position": 1,
                "snapshot_kind": "legacy_contents",
            }
        ],
    )

    assert report["safe"] is False
    assert report["counts"]["box_files"] == 3
    assert len(report["conflicts"]["duplicate_references"]) == 1
    assert report["conflicts"]["cross_lot_placements"][0]["file_id"] == 12
    assert report["conflicts"]["blank_file_references"] == [13]
    assert report["conflicts"]["request_snapshot_coverage"][0]["request_item_id"] == 21
