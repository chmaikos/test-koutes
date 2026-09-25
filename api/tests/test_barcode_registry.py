from __future__ import annotations

import importlib.util
import io
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.barcode_identities import (
    BarcodeEntityKind,
    BarcodeFileMigrationAudit,
    BarcodeIdentity,
)
from app.models.box_files import BoxFile, BoxFileEvent
from app.models.boxes import Box, BoxEvent, BoxStatus
from app.models.lots import Lot, LotEvent
from app.models.pallets import Pallet, PalletEvent
from app.models.requests import BoxRequestItem, BoxRequestItemFileSnapshot
from app.models.users import UserRole
from app.services.barcodes import (
    BarcodeNotFoundError,
    BarcodeRetirementTarget,
    barcode_identity_lock_statement,
    format_barcode,
    is_valid_barcode,
    issue_barcode_identity,
    mod10_check_digit,
    parse_barcode,
    resolve_barcode,
    retire_barcode_identity,
)
from app.services.box_files import archive_box_file, move_box_file, restore_box_file
from app.services.boxes import create_box, delete_box, reassign_box_lot
from app.services.lots import get_or_create_lot
from scripts.barcode_preflight import analyze_rows


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0036_barcode_registry.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0036_barcode_registry", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _operations(connection: sa.Connection) -> Operations:
    return Operations(MigrationContext.configure(connection))


def _pre_0036_database() -> sa.Engine:
    engine = sa.create_engine("sqlite://", future=True)
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    for table_name in ("lots", "pallets", "boxes"):
        sa.Table(table_name, metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table(
        "box_files",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("barcode", sa.String(255)),
        sa.Index("ix_box_files_barcode", "barcode"),
    )
    sa.Table(
        "box_request_items",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lot_id", sa.Integer()),
        sa.Column("pallet_id", sa.Integer()),
        sa.Column("box_id", sa.Integer()),
    )
    sa.Table(
        "box_request_item_file_snapshots",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_id", sa.Integer()),
        sa.Column("barcode", sa.String(255)),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("INSERT INTO lots (id) VALUES (8), (2)"))
        connection.execute(sa.text("INSERT INTO pallets (id) VALUES (7)"))
        connection.execute(sa.text("INSERT INTO boxes (id) VALUES (5)"))
        connection.execute(
            sa.text(
                """
                INSERT INTO box_request_items (id, lot_id, pallet_id, box_id)
                VALUES (1, 2, 7, 5), (2, NULL, NULL, NULL)
                """
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO box_files (id, barcode) "
                "VALUES (20, 'DUPLICATE'), (10, 'DUPLICATE')"
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO box_request_item_file_snapshots (id, file_id, barcode)
                VALUES (1, 10, 'DUPLICATE'), (2, NULL, NULL)
                """
            )
        )
    return engine


def test_format_checksum_and_validation_are_deterministic() -> None:
    assert mod10_check_digit(1) == 7
    assert format_barcode(BarcodeEntityKind.lot, 1) == "LOT-000000000001-7"
    assert format_barcode("file", 42) == "FIL-000000000042-0"
    assert parse_barcode("BOX-000000000123-6") == (BarcodeEntityKind.box, 123)
    assert is_valid_barcode("PAL-000000000002-4")
    assert not is_valid_barcode("PAL-000000000002-5")
    with pytest.raises(ValueError, match="namespace"):
        format_barcode("lot", 0)


def test_bulk_retirement_lock_order_is_cross_database_deterministic() -> None:
    statement = barcode_identity_lock_statement(
        [
            BarcodeRetirementTarget(BarcodeEntityKind.file, 8, {}),
            BarcodeRetirementTarget(BarcodeEntityKind.lot, 9, {}),
            BarcodeRetirementTarget(BarcodeEntityKind.box, 2, {}),
        ]
    )
    postgres_sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    sqlite_sql = str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "ORDER BY CASE WHEN (barcode_identities.entity_kind = 'lot')" in postgres_sql
    assert postgres_sql.endswith("FOR UPDATE OF barcode_identities")
    assert "ORDER BY CASE WHEN (barcode_identities.entity_kind = 'lot')" in sqlite_sql
    assert "FOR UPDATE" not in sqlite_sql


def test_test_session_hook_issues_read_only_model_identity(session: Session) -> None:
    lot = Lot(name="Barcode Lot")
    session.add(lot)
    session.commit()
    assert lot.barcode.startswith("LOT-")
    assert lot.barcode_identity.entity_id == lot.id
    assert lot.barcode_identity_id == lot.barcode_identity.id
    with pytest.raises(AttributeError):
        lot.barcode = "LOT-000000000999-0"  # type: ignore[misc]


def test_sqlite_migration_backfill_immutability_and_audit() -> None:
    migration = _load_migration()
    engine = _pre_0036_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()

        rows = connection.execute(
            sa.text(
                """
                SELECT entity_kind, entity_id, issuance_number, barcode
                FROM barcode_identities
                ORDER BY issuance_number
                """
            )
        ).all()
        assert rows == [
            ("lot", 2, 1, "LOT-000000000001-7"),
            ("lot", 8, 2, "LOT-000000000002-4"),
            ("pallet", 7, 3, "PAL-000000000003-1"),
            ("box", 5, 4, "BOX-000000000004-8"),
            ("file", 10, 5, "FIL-000000000005-5"),
            ("file", 20, 6, "FIL-000000000006-2"),
        ]
        assert "barcode" not in {
            column["name"] for column in sa.inspect(connection).get_columns("box_files")
        }
        assert connection.execute(
            sa.text(
                "SELECT file_id, old_barcode, new_barcode "
                "FROM barcode_file_migration_audit ORDER BY file_id"
            )
        ).all() == [
            (10, "DUPLICATE", "FIL-000000000005-5"),
            (20, "DUPLICATE", "FIL-000000000006-2"),
        ]
        assert connection.execute(
            sa.text(
                "SELECT id, barcode FROM box_request_item_file_snapshots ORDER BY id"
            )
        ).all() == [(1, "FIL-000000000005-5"), (2, None)]
        assert connection.execute(
            sa.text(
                "SELECT id, lot_barcode, pallet_barcode, box_barcode "
                "FROM box_request_items ORDER BY id"
            )
        ).all() == [
            (
                1,
                "LOT-000000000001-7",
                "PAL-000000000003-1",
                "BOX-000000000004-8",
            ),
            (2, None, None, None),
        ]

        connection.execute(
            sa.text(
                "UPDATE box_request_items "
                "SET box_id = 5, box_barcode = 'BOX-000000000004-8' "
                "WHERE id = 2"
            )
        )
        with pytest.raises(sa.exc.DatabaseError, match="write-once"):
            connection.execute(
                sa.text(
                    "UPDATE box_request_items SET box_barcode = NULL WHERE id = 2"
                )
            )
        connection.execute(
            sa.text(
                "UPDATE box_request_item_file_snapshots "
                "SET file_id = 20, barcode = 'FIL-000000000006-2' WHERE id = 2"
            )
        )
        with pytest.raises(sa.exc.DatabaseError, match="write-once"):
            connection.execute(
                sa.text(
                    "UPDATE box_request_item_file_snapshots "
                    "SET barcode = 'FIL-000000000005-5' WHERE id = 2"
                )
            )

        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            connection.execute(
                sa.text("UPDATE barcode_identities SET issuance_number = 99 WHERE id = 1")
            )
        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            connection.execute(
                sa.text("UPDATE lots SET barcode_identity_id = 2 WHERE id = 2")
            )
        with pytest.raises(sa.exc.DatabaseError, match="permanently reserved"):
            connection.execute(sa.text("DELETE FROM barcode_identities WHERE id = 1"))

        # Hard deletion is forbidden until the permanent identity is retired.
        with pytest.raises(sa.exc.DatabaseError, match="retired before deletion"):
            connection.execute(sa.text("DELETE FROM lots WHERE id = 8"))
        connection.execute(
            sa.text(
                """
                UPDATE barcode_identities
                SET retired_at = CURRENT_TIMESTAMP,
                    retirement_reason = 'migration test retirement',
                    retirement_metadata = '{"operation":"test"}'
                WHERE id = 2
                """
            )
        )
        connection.execute(sa.text("DELETE FROM lots WHERE id = 8"))
        assert connection.scalar(
            sa.text("SELECT count(*) FROM barcode_identities WHERE id = 2")
        ) == 1
        connection.execute(
            sa.text(
                "UPDATE barcode_identities "
                "SET retired_at = CURRENT_TIMESTAMP, "
                "retirement_reason = 'file purge test' WHERE id = 5"
            )
        )
        connection.execute(sa.text("DELETE FROM box_files WHERE id = 10"))
        assert connection.scalar(
            sa.text(
                "SELECT count(*) FROM barcode_file_migration_audit "
                "WHERE file_id = 10 AND old_barcode = 'DUPLICATE'"
            )
        ) == 1
    engine.dispose()


def test_sqlite_duplicate_registry_values_and_guarded_downgrade() -> None:
    migration = _load_migration()
    engine = _pre_0036_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO barcode_identities (
                        id, entity_kind, issuance_number, barcode, entity_id,
                        issuance_reason, metadata
                    ) VALUES (
                        1, 'lot', 1, 'LOT-000000000001-7', 999, 'later', '{}'
                    )
                    """
                )
            )
        connection.execute(
            sa.text(
                """
                INSERT INTO barcode_identities (
                    id, entity_kind, issuance_number, barcode, entity_id,
                    issuance_reason, metadata
                ) VALUES (
                    7, 'lot', 7, 'LOT-000000000007-9', 999, 'later issuance', '{}'
                )
                """
            )
        )
        with pytest.raises(RuntimeError, match="post-migration"):
            migration.downgrade()
    engine.dispose()


def test_safe_downgrade_restores_legacy_file_barcodes() -> None:
    migration = _load_migration()
    engine = _pre_0036_database()
    with engine.begin() as connection:
        migration.op = _operations(connection)
        migration.upgrade()
        migration.downgrade()
        assert connection.execute(
            sa.text("SELECT id, barcode FROM box_files ORDER BY id")
        ).all() == [(10, "DUPLICATE"), (20, "DUPLICATE")]
    engine.dispose()


def test_offline_postgresql_contains_sequence_backfill_and_guards() -> None:
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
    assert "CREATE SEQUENCE barcode_identity_number_seq" in sql
    assert "barcode_registry_format" in sql
    assert "FOR entity_row IN SELECT id FROM lots ORDER BY id" in sql
    assert "FOR entity_row IN SELECT id FROM box_files ORDER BY id" in sql
    assert "barcode identities are permanently reserved" in sql
    assert "barcode identity link is immutable" in sql
    assert "post-migration barcode identities exist" in sql


def test_preflight_reports_malformed_broken_and_deleted_rows() -> None:
    report = analyze_rows(
        [
            {
                "id": 1,
                "entity_kind": "lot",
                "issuance_number": 1,
                "barcode": "LOT-000000000001-0",
                "entity_id": 5,
                "retired_at": None,
            }
        ],
        {
            BarcodeEntityKind.lot: [{"id": 6, "barcode_identity_id": 1}],
            BarcodeEntityKind.pallet: [],
            BarcodeEntityKind.box: [],
            BarcodeEntityKind.file: [],
        },
        mutable_legacy_barcode_column=True,
    )
    assert not report["safe"]
    assert report["conflicts"]["malformed_barcodes"]
    assert report["conflicts"]["broken_identity_links"]
    assert report["conflicts"]["deleted_objects_with_unretired_identity"]
    assert report["conflicts"]["unexpected_mutable_legacy_inputs"]


def test_explicit_creation_paths_finalize_system_owned_identities(
    session: Session,
    make_user,
) -> None:
    operator = make_user(UserRole.operator)
    box = create_box(
        session,
        user=operator,
        box_number="1",
        lot="Lifecycle Lot",
        pallet_number="Lifecycle Pallet",
        warehouse_id=1,
        files=[SimpleNamespace(reference="FILE-1", description="First")],
    )
    session.refresh(box)
    assert box.barcode_identity.issued_by_user_id == operator.id
    assert box.barcode_identity.issuance_reason == "Box created through receipt intake."
    assert box.lot_record.barcode_identity.issued_by_user_id == operator.id
    assert box.lot_record.barcode_identity.issuance_metadata["operation"] == "get_or_create"
    assert len(box.files) == 1
    assert box.files[0].barcode_identity.issued_by_user_id == operator.id
    assert (
        box.files[0].barcode_identity.issuance_metadata["operation"]
        == "intake_reconciliation"
    )
    assert session.scalar(
        select(LotEvent).where(LotEvent.lot_id == box.lot_id)
    ).event_metadata["lot_barcode"] == box.lot_record.barcode
    assert session.scalar(
        select(PalletEvent).where(PalletEvent.pallet_id == box.pallet_id)
    ).event_metadata["pallet_barcode"] == box.pallet.barcode
    assert session.scalar(
        select(BoxEvent).where(BoxEvent.box_id == box.id)
    ).event_metadata["box_barcode"] == box.barcode
    file_event = session.scalar(
        select(BoxFileEvent).where(BoxFileEvent.file_id == box.files[0].id)
    )
    assert file_event.event_metadata["file_barcode"] == box.files[0].barcode
    assert file_event.after_snapshot["file_barcode"] == box.files[0].barcode
    assert file_event.after_snapshot["box_barcode"] == box.barcode


def test_request_barcode_snapshots_are_write_once_in_orm() -> None:
    item = BoxRequestItem(
        request_id=1,
        position=1,
        lot_barcode=None,
        pallet_barcode=None,
        box_barcode=None,
    )
    item.lot_barcode = "LOT-000000000001-7"
    item.pallet_barcode = "PAL-000000000002-4"
    item.box_barcode = "BOX-000000000003-1"
    with pytest.raises(ValueError, match="write-once"):
        item.box_barcode = "BOX-000000000004-8"

    snapshot = BoxRequestItemFileSnapshot(
        request_item_id=1,
        file_id=None,
        reference="staged",
        description=None,
        barcode=None,
        position=1,
        snapshot_kind="tracked_file",
    )
    snapshot.file_id = 9
    snapshot.barcode = "FIL-000000000005-5"
    with pytest.raises(ValueError, match="immutable"):
        snapshot.barcode = "FIL-000000000006-2"


def test_sqlite_rollback_never_reuses_an_observed_issuance_number(
    session: Session,
) -> None:
    rolled_back = Lot(name="Rolled Back")
    session.add(rolled_back)
    first = issue_barcode_identity(session, rolled_back, reason="rollback test")
    first_number = first.issuance_number
    session.flush()
    session.rollback()

    committed = Lot(name="Committed")
    session.add(committed)
    second = issue_barcode_identity(session, committed, reason="rollback test")
    session.commit()

    assert second.issuance_number > first_number


def test_ordinary_box_delete_retires_permanent_registry_with_context(
    session: Session,
    make_user,
) -> None:
    operator = make_user(UserRole.operator)
    box = create_box(
        session,
        user=operator,
        box_number="9",
        lot="Delete Lifecycle",
        warehouse_id=1,
    )
    identity_id = box.barcode_identity_id

    outcome = delete_box(session, user=operator, box=box)

    assert not outcome.archived
    assert session.get(Box, box.id) is None
    identity = session.get(BarcodeIdentity, identity_id)
    assert identity is not None
    assert identity.retired_at is not None
    assert identity.retired_by_user_id == operator.id
    assert identity.retirement_metadata["operation"] == "ordinary_box_hard_delete"
    assert identity.retirement_metadata["hierarchy"]["lot_id"] == box.lot_id


def test_moves_reassignment_and_archive_restore_preserve_identities(
    session: Session,
    make_user,
) -> None:
    admin = make_user(UserRole.admin)
    source = create_box(
        session,
        user=admin,
        box_number="21",
        lot="Stable Source",
        warehouse_id=1,
        files=[SimpleNamespace(reference="STABLE-FILE", description=None)],
    )
    target = create_box(
        session,
        user=admin,
        box_number="22",
        lot_id=source.lot_id,
        warehouse_id=1,
    )
    file = source.files[0]
    box_identity_id = source.barcode_identity_id
    file_identity_id = file.barcode_identity_id
    file_barcode = file.barcode

    moved = move_box_file(
        session,
        user=admin,
        file_id=file.id,
        target_box_id=target.id,
        expected_version=file.version,
        reason="Lifecycle stability test.",
    )
    archived = archive_box_file(
        session,
        user=admin,
        file_id=moved.id,
        expected_version=moved.version,
        reason="Lifecycle stability test.",
    )
    restored = restore_box_file(
        session,
        user=admin,
        file_id=archived.id,
        expected_version=archived.version,
        reason="Lifecycle stability test.",
    )
    destination_lot = get_or_create_lot(
        session,
        user=admin,
        name="Stable Destination",
        warehouse_id=1,
    )
    reassigned = reassign_box_lot(
        session,
        user=admin,
        box=source,
        lot_id=destination_lot.id,
        reason="Lifecycle stability test.",
        expected_version=source.lot_record.version,
    )

    assert reassigned.barcode_identity_id == box_identity_id
    assert restored.barcode_identity_id == file_identity_id
    assert restored.barcode == file_barcode
    assert restored.barcode_identity.retired_at is None


def _resolver_graph(session: Session) -> tuple[Lot, Pallet, Box, BoxFile]:
    lot = Lot(name="Resolver Lot")
    session.add(lot)
    session.flush()
    pallet = Pallet(lot_id=lot.id, pallet_number="Resolver Pallet")
    session.add(pallet)
    session.flush()
    box = Box(
        lot_id=lot.id,
        pallet_id=pallet.id,
        box_number="077",
        current_warehouse_id=1,
    )
    session.add(box)
    session.flush()
    file = BoxFile(
        lot_id=lot.id,
        box_id=box.id,
        reference="Resolver File",
        position=1,
    )
    session.add(file)
    session.commit()
    return lot, pallet, box, file


def test_barcode_api_resolves_all_kinds_and_exposes_read_contracts(
    client,
    session: Session,
) -> None:
    lot, pallet, box, file = _resolver_graph(session)

    expectations = (
        (lot, "lot", f"/lots/{lot.id}", "Lot Resolver Lot"),
        (pallet, "pallet", f"/pallets/{pallet.id}", "Pallet Resolver Pallet"),
        (box, "box", f"/boxes/{box.id}", "Box 077"),
        (file, "file", f"/files/{file.id}", "File Resolver File"),
    )
    for entity, kind, path, label in expectations:
        response = client.get(f"/api/barcodes/{entity.barcode}")
        assert response.status_code == 200
        body = response.json()
        assert body["entity_kind"] == kind
        assert body["entity_id"] == entity.id
        assert body["barcode"] == entity.barcode
        assert body["lifecycle_state"] == "active"
        assert body["frontend_path"] == path
        assert body["display_label"] == label
        assert body["retired"] is False

    assert client.get("/api/lots").json()["items"][0]["barcode"] == lot.barcode
    assert client.get("/api/lots/options").json()["items"][0]["barcode"] == lot.barcode
    assert client.get("/api/pallets").json()["items"][0]["barcode"] == pallet.barcode
    assert (
        client.get(f"/api/pallets/options?lot_id={lot.id}").json()["items"][0][
            "barcode"
        ]
        == pallet.barcode
    )
    assert client.get("/api/boxes").json()["items"][0]["barcode"] == box.barcode
    assert client.get("/api/files").json()["items"][0]["barcode"] == file.barcode

    session.add(
        BarcodeFileMigrationAudit(
            file_id=file.id,
            identity_id=file.barcode_identity_id,
            issuance_number=file.barcode_identity.issuance_number,
            old_barcode="LEGACY-FILE-CODE",
            new_barcode=file.barcode,
        )
    )
    session.commit()
    audit = client.get("/api/barcodes/migration-audit/files")
    assert audit.status_code == 200
    assert audit.json()[0]["old_barcode"] == "LEGACY-FILE-CODE"
    assert client.get("/api/barcodes/LEGACY-FILE-CODE").status_code == 400


def test_barcode_api_rejects_invalid_checksum_and_hides_unknown(
    client,
) -> None:
    invalid = client.get("/api/barcodes/BOX-000000000123-5")
    assert invalid.status_code == 400
    assert "check digit" in invalid.json()["detail"]
    assert client.get("/api/barcodes/BOX-999999999999-4").status_code == 404
    assert client.get("/api/barcodes/box-000000000123-6").status_code == 400


def test_resolver_acl_spanning_hierarchy_and_retired_admin_tombstone(
    session: Session,
    make_user,
) -> None:
    lot, pallet, box, file = _resolver_graph(session)
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    operator.warehouses = [operator.warehouses[0]]
    session.add(
        Box(
            lot_id=lot.id,
            pallet_id=pallet.id,
            box_number="078",
            current_warehouse_id=2,
        )
    )
    session.commit()

    pallet_result = resolve_barcode(session, user=operator, barcode=pallet.barcode)
    assert pallet_result["hierarchy"]["visible_warehouse_ids"] == [1]  # type: ignore[index]
    box.current_warehouse_id = 2
    session.commit()
    with pytest.raises(BarcodeNotFoundError):
        resolve_barcode(session, user=operator, barcode=box.barcode)
    with pytest.raises(BarcodeNotFoundError):
        resolve_barcode(session, user=operator, barcode=file.barcode)

    retire_barcode_identity(
        box.barcode_identity,
        actor_user_id=admin.id,
        reason="permanent test retirement",
        metadata={
            "operation": "test_purge",
            "hierarchy": {"box_id": box.id, "warehouse_id": 2},
        },
    )
    session.commit()
    with pytest.raises(BarcodeNotFoundError):
        resolve_barcode(session, user=operator, barcode=box.barcode)
    retired = resolve_barcode(session, user=admin, barcode=box.barcode)
    assert retired["retired"] is True
    assert retired["lifecycle_state"] == "retired"
    assert retired["frontend_path"] is None
    assert retired["retirement_operation"] == "test_purge"


def test_resolver_lifecycle_states_and_authorized_redirects(
    session: Session,
    make_user,
) -> None:
    operator = make_user(UserRole.operator)
    admin = make_user(UserRole.admin)
    target_lot, target_pallet, box, file = _resolver_graph(session)

    source_lot = Lot(name="Merged Resolver Source")
    session.add(source_lot)
    session.flush()
    source_lot.merged_into_lot_id = target_lot.id
    source_lot.merged_at = datetime.now(UTC)
    source_lot.normalized_name = None

    absorbed = Pallet(
        lot_id=target_lot.id,
        pallet_number="Absorbed Resolver Pallet",
        is_active=False,
        archived_at=datetime.now(UTC),
        absorbed_into_pallet_id=target_pallet.id,
        absorbed_at=datetime.now(UTC),
    )
    archived = Pallet(
        lot_id=target_lot.id,
        pallet_number="Archived Resolver Pallet",
        is_active=False,
        archived_at=datetime.now(UTC),
    )
    session.add_all([absorbed, archived])
    session.commit()

    merged_result = resolve_barcode(
        session, user=operator, barcode=source_lot.barcode
    )
    assert merged_result["lifecycle_state"] == "merged"
    assert merged_result["redirect"]["entity_id"] == target_lot.id  # type: ignore[index]
    absorbed_result = resolve_barcode(
        session, user=operator, barcode=absorbed.barcode
    )
    assert absorbed_result["lifecycle_state"] == "absorbed"
    assert absorbed_result["redirect"]["entity_id"] == target_pallet.id  # type: ignore[index]

    with pytest.raises(BarcodeNotFoundError):
        resolve_barcode(session, user=operator, barcode=archived.barcode)
    assert (
        resolve_barcode(session, user=admin, barcode=archived.barcode)[
            "lifecycle_state"
        ]
        == "archived"
    )

    box.status = BoxStatus.returned
    file.archived_at = datetime.now(UTC)
    session.commit()
    assert (
        resolve_barcode(session, user=operator, barcode=box.barcode)[
            "lifecycle_state"
        ]
        == "returned"
    )
    assert (
        resolve_barcode(session, user=operator, barcode=file.barcode)[
            "lifecycle_state"
        ]
        == "archived"
    )


def test_barcode_searches_are_partial_and_pagination_stays_stable(
    client,
    session: Session,
) -> None:
    lot, pallet, box, file = _resolver_graph(session)
    endpoints = (
        ("/api/lots", lot),
        ("/api/lots/options", lot),
        ("/api/pallets", pallet),
        (f"/api/pallets/options?lot_id={lot.id}", pallet),
        ("/api/boxes", box),
        ("/api/files", file),
    )
    for endpoint, entity in endpoints:
        separator = "&" if "?" in endpoint else "?"
        fragment = entity.barcode[4:15]
        response = client.get(
            f"{endpoint}{separator}search={fragment}&page=1&page_size=1"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == entity.id
        assert body["items"][0]["barcode"] == entity.barcode
