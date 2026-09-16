from __future__ import annotations

import importlib.util
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.db import Base
from app.deps import get_current_user
from app.main import app
from app.models import (
    Box,
    BoxRequest,
    BoxRequestDirection,
    BoxRequestItem,
    BoxRequestOrigin,
    BoxRequestStatus,
    BoxStatus,
    Lot,
    Pallet,
    PalletEvent,
    PalletEventType,
    UserRole,
    Warehouse,
)
from app.schemas.pallets import PalletDetailOut, PalletOptionOut, PalletSummaryOut


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0033_organizational_pallets.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0033", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lot(session, name: str) -> Lot:
    lot = Lot(name=name)
    session.add(lot)
    session.flush()
    return lot


def _restrict_to(session, user, *warehouse_ids: int) -> None:
    user.warehouses = [
        session.get(Warehouse, warehouse_id) for warehouse_id in warehouse_ids
    ]
    session.commit()


def test_multi_warehouse_summary_is_acl_scoped_and_filter_uses_boxes(
    client, session, make_user
) -> None:
    lot = _lot(session, "Distributed")
    pallet = Pallet(lot_id=lot.id, pallet_number="ORG-1")
    session.add(pallet)
    session.flush()
    session.add_all(
        [
            Box(
                box_number="001",
                lot_id=lot.id,
                pallet_id=pallet.id,
                current_warehouse_id=1,
                status=BoxStatus.received,
            ),
            Box(
                box_number="002",
                lot_id=lot.id,
                pallet_id=pallet.id,
                current_warehouse_id=2,
                status=BoxStatus.ready_to_return,
            ),
        ]
    )
    session.commit()

    admin_detail = client.get(f"/api/pallets/{pallet.id}")
    assert admin_detail.status_code == 200
    assert admin_detail.json()["warehouse_ids"] == [1, 2]
    assert admin_detail.json()["warehouse_names"] == ["Building 1", "Building 2"]
    assert admin_detail.json()["box_count"] == 2

    operator = make_user(UserRole.operator)
    _restrict_to(session, operator, 1)
    app.dependency_overrides[get_current_user] = lambda: operator
    partial = client.get(f"/api/pallets/{pallet.id}")
    assert partial.status_code == 200
    assert partial.json()["warehouse_ids"] == [1]
    assert partial.json()["warehouse_names"] == ["Building 1"]
    assert partial.json()["box_count"] == 1
    assert partial.json()["status_counts"]["received"] == 1
    assert partial.json()["status_counts"]["ready_to_return"] == 0

    visible_filter = client.get("/api/pallets", params={"warehouse_id": 1})
    hidden_filter = client.get("/api/pallets", params={"warehouse_id": 2})
    assert [row["id"] for row in visible_filter.json()["items"]] == [pallet.id]
    assert hidden_filter.json()["items"] == []
    option = client.get("/api/pallets/options").json()["items"][0]
    assert option["warehouse_ids"] == [1]
    assert option["warehouse_names"] == ["Building 1"]


def test_empty_visibility_uses_admin_or_accessible_staged_lot_context(
    client, session, make_user
) -> None:
    lot = _lot(session, "Staged Empty")
    pallet = Pallet(lot_id=lot.id, pallet_number="EMPTY")
    session.add(pallet)
    session.commit()
    assert client.get(f"/api/pallets/{pallet.id}").status_code == 200

    operator = make_user(UserRole.operator)
    _restrict_to(session, operator, 1)
    app.dependency_overrides[get_current_user] = lambda: operator
    assert client.get(f"/api/pallets/{pallet.id}").status_code == 404

    request = BoxRequest(
        direction=BoxRequestDirection.inbound,
        warehouse_id=1,
        quantity=1,
        status=BoxRequestStatus.submitted,
        origin=BoxRequestOrigin.manual_entry,
        requester_user_id=operator.id,
    )
    session.add(request)
    session.flush()
    session.add(
        BoxRequestItem(
            request_id=request.id,
            position=1,
            lot_id=lot.id,
            lot=lot.name,
            box_number="001",
            pallet_id=pallet.id,
            pallet=pallet.pallet_number,
        )
    )
    session.commit()
    staged = client.get(f"/api/pallets/{pallet.id}")
    assert staged.status_code == 200
    assert staged.json()["warehouse_ids"] == []
    assert staged.json()["box_count"] == 0


def test_operator_can_create_empty_pallet_with_authorization_context(
    client, session, make_user
) -> None:
    lot = _lot(session, "Authorized Empty")
    session.commit()
    operator = make_user(UserRole.operator)
    _restrict_to(session, operator, 1)
    app.dependency_overrides[get_current_user] = lambda: operator

    created = client.post(
        "/api/pallets",
        json={
            "lot_id": lot.id,
            "warehouse_id": 1,
            "pallet_number": "CONTEXT-ONLY",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["warehouse_ids"] == []
    pallet = session.scalar(select(Pallet).where(Pallet.id == created.json()["id"]))
    assert pallet is not None
    assert "current_warehouse_id" not in sa.inspect(Pallet).columns


def test_assignment_accepts_same_lot_boxes_across_warehouses(
    client, session, make_user
) -> None:
    lot = _lot(session, "Cross Warehouse Assignment")
    pallet = Pallet(lot_id=lot.id, pallet_number="ORG")
    session.add(pallet)
    session.flush()
    boxes = [
        Box(
            box_number=str(index),
            lot_id=lot.id,
            current_warehouse_id=warehouse_id,
            status=BoxStatus.received,
        )
        for index, warehouse_id in ((1, 1), (2, 2))
    ]
    session.add_all(boxes)
    session.commit()
    operator = make_user(UserRole.operator)
    _restrict_to(session, operator, 1, 2)
    app.dependency_overrides[get_current_user] = lambda: operator

    assigned = client.post(
        f"/api/pallets/{pallet.id}/boxes/assign",
        json={"box_ids": [box.id for box in boxes], "reason": "Organize lot"},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["updated_box_ids"] == [box.id for box in boxes]
    session.expire_all()
    assert {session.get(Box, box.id).pallet_id for box in boxes} == {pallet.id}


def test_normal_and_forced_bulk_moves_preserve_pallet_membership(
    client, session
) -> None:
    lot = _lot(session, "Bulk Preserve")
    pallet = Pallet(lot_id=lot.id, pallet_number="BULK")
    session.add(pallet)
    session.flush()
    received = Box(
        box_number="001",
        lot_id=lot.id,
        pallet_id=pallet.id,
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    returned = Box(
        box_number="002",
        lot_id=lot.id,
        pallet_id=pallet.id,
        current_warehouse_id=1,
        status=BoxStatus.returned,
        returned_at=datetime.now(UTC),
    )
    session.add_all([received, returned])
    session.commit()

    normal = client.post(
        "/api/boxes/bulk",
        json={
            "box_ids": [received.id],
            "warehouse_id": 2,
            "note": "Ordinary transfer",
        },
    )
    forced = client.post(
        "/api/boxes/bulk",
        json={
            "box_ids": [returned.id],
            "warehouse_id": 3,
            "force": True,
            "note": "Returned inventory correction",
        },
    )
    assert normal.status_code == forced.status_code == 200
    session.expire_all()
    assert session.get(Box, received.id).pallet_id == pallet.id
    assert session.get(Box, returned.id).pallet_id == pallet.id
    assert session.get(Box, received.id).current_warehouse_id == 2
    assert session.get(Box, returned.id).current_warehouse_id == 3
    assert session.get(Pallet, pallet.id).is_active is True


def test_import_reuses_lot_pallet_across_receipt_warehouses(client) -> None:
    payload = {
        "items": [
            {
                "box_number": "001",
                "lot": "Import Distributed",
                "pallet_number": "IMPORT-ORG",
            }
        ]
    }
    first = client.post(
        "/api/boxes/import-mapped",
        json={"warehouse_id": 1, **payload},
    )
    payload["items"][0]["box_number"] = "002"
    second = client.post(
        "/api/boxes/import-mapped",
        json={"warehouse_id": 2, **payload},
    )
    assert first.status_code == second.status_code == 200
    assert (
        first.json()["created"][0]["pallet_id"]
        == second.json()["created"][0]["pallet_id"]
    )


def test_pallet_assignment_sse_fans_out_sources_targets_and_box_warehouses(
    client, session, monkeypatch
) -> None:
    lot = _lot(session, "SSE Fanout")
    source = Pallet(lot_id=lot.id, pallet_number="SOURCE")
    target = Pallet(lot_id=lot.id, pallet_number="TARGET")
    session.add_all([source, target])
    session.flush()
    boxes = [
        Box(
            box_number=str(index),
            lot_id=lot.id,
            pallet_id=source.id,
            current_warehouse_id=warehouse_id,
            status=BoxStatus.received,
        )
        for index, warehouse_id in ((1, 1), (2, 2))
    ]
    session.add_all(boxes)
    session.commit()
    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr("app.routers.pallets.bus.publish", capture)
    response = client.post(
        f"/api/pallets/{target.id}/boxes/assign",
        json={
            "box_ids": [box.id for box in boxes],
            "reason": "Reorganize distributed pallet",
        },
    )
    assert response.status_code == 200
    pallet_events = {
        (int(data["id"]), int(data["warehouse_id"]))
        for event_type, data in published
        if event_type == "pallet.updated"
    }
    assert pallet_events == {
        (source.id, 1),
        (source.id, 2),
        (target.id, 1),
        (target.id, 2),
    }
    box_events = {
        int(data["warehouse_id"])
        for event_type, data in published
        if event_type == "box.updated"
    }
    assert box_events == {1, 2}


def test_pallet_rename_sse_fans_out_box_warehouses(
    client, session, monkeypatch
) -> None:
    lot = _lot(session, "Rename Fanout")
    pallet = Pallet(lot_id=lot.id, pallet_number="BEFORE")
    session.add(pallet)
    session.flush()
    session.add_all(
        [
            Box(
                box_number=str(index),
                lot_id=lot.id,
                pallet_id=pallet.id,
                current_warehouse_id=warehouse_id,
                status=BoxStatus.received,
            )
            for index, warehouse_id in ((1, 1), (2, 2))
        ]
    )
    session.commit()
    published: list[tuple[str, dict[str, object]]] = []

    async def capture(event_type: str, data: dict[str, object]) -> None:
        published.append((event_type, data))

    monkeypatch.setattr("app.routers.pallets.bus.publish", capture)
    response = client.patch(
        f"/api/pallets/{pallet.id}/rename",
        json={
            "new_pallet_number": "AFTER",
            "reason": "Correct label",
            "expected_version": pallet.version,
        },
    )

    assert response.status_code == 200, response.text
    assert {
        (payload["id"], payload["warehouse_id"])
        for event_type, payload in published
        if event_type == "pallet.renamed"
    } == {(pallet.id, 1), (pallet.id, 2)}


def test_nonadmin_pallet_events_redact_inaccessible_counts_and_warehouses(
    client, session, make_user
) -> None:
    lot = _lot(session, "Event ACL")
    pallet = Pallet(lot_id=lot.id, pallet_number="EVENTS")
    session.add(pallet)
    session.flush()
    box = Box(
        box_number="001",
        lot_id=lot.id,
        pallet_id=pallet.id,
        current_warehouse_id=1,
        status=BoxStatus.received,
    )
    session.add(box)
    session.flush()
    session.add(
        PalletEvent(
            pallet_id=pallet.id,
            event_type=PalletEventType.boxes_assigned,
            from_warehouse_id=1,
            to_warehouse_id=2,
            event_metadata={
                "operation": "bulk_assign",
                "box_ids": [box.id, 999999],
                "box_count": 2,
                "source_warehouse_id": 2,
            },
        )
    )
    session.commit()

    operator = make_user(UserRole.operator)
    _restrict_to(session, operator, 1)
    app.dependency_overrides[get_current_user] = lambda: operator
    restricted = client.get(f"/api/pallets/{pallet.id}/events")
    assert restricted.status_code == 200
    assert restricted.json()[0]["from_warehouse_id"] == 1
    assert restricted.json()[0]["to_warehouse_id"] is None
    assert restricted.json()[0]["metadata"] == {"operation": "bulk_assign"}

    admin = make_user(UserRole.admin)
    app.dependency_overrides[get_current_user] = lambda: admin
    unrestricted = client.get(f"/api/pallets/{pallet.id}/events")
    assert unrestricted.status_code == 200
    assert unrestricted.json()[0]["to_warehouse_id"] == 2
    assert unrestricted.json()[0]["metadata"]["box_count"] == 2


def test_restore_requires_empty_pallet(client, session) -> None:
    lot = _lot(session, "Restore Guard")
    pallet = Pallet(
        lot_id=lot.id,
        pallet_number="OLD",
        is_active=False,
        archived_at=datetime.now(UTC),
    )
    session.add(pallet)
    session.flush()
    session.add(
        Box(
            box_number="001",
            lot_id=lot.id,
            pallet_id=pallet.id,
            current_warehouse_id=2,
            status=BoxStatus.received,
        )
    )
    session.commit()

    response = client.post(
        f"/api/pallets/{pallet.id}/restore",
        json={"reason": "Should fail", "expected_version": pallet.version},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pallet_not_empty"


def test_contracts_have_only_derived_pallet_warehouses() -> None:
    for contract in (PalletSummaryOut, PalletDetailOut, PalletOptionOut):
        assert "current_warehouse_id" not in contract.model_fields
        assert "warehouse_name" not in contract.model_fields
        assert "warehouse_ids" in contract.model_fields
        assert "warehouse_names" in contract.model_fields


def _sqlite_0032_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "ALTER TABLE pallets ADD COLUMN "
                "current_warehouse_id INTEGER REFERENCES warehouses(id)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE INDEX ix_pallets_warehouse_active "
                "ON pallets (current_warehouse_id, is_active)"
            )
        )
    return engine


def test_0033_sqlite_upgrade_and_safe_downgrade_preserve_assignments() -> None:
    migration = _load_migration()
    engine = _sqlite_0032_engine()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO lots (id, name, normalized_name, version) "
                "VALUES (10, 'Migration', 'migration', 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO pallets "
                "(id, lot_id, current_warehouse_id, pallet_number, "
                "normalized_pallet_number, version, is_active) "
                "VALUES (20, 10, 1, 'P-1', 'p-1', 1, 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO boxes "
                "(id, box_number, lot_id, current_warehouse_id, status, pallet_id) "
                "VALUES (30, '001', 10, 1, 'received', 20), "
                "(31, '002', 10, 1, 'received', 20)"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        inspector = sa.inspect(connection)
        assert "current_warehouse_id" not in {
            column["name"] for column in inspector.get_columns("pallets")
        }
        assert {index["name"] for index in inspector.get_indexes("pallets")} == {
            "ix_pallets_absorbed_into",
            "ix_pallets_lot_active",
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints("pallets")
        } == {
            "ck_pallets_absorbed_state",
            "ck_pallets_archive_state",
            "ck_pallets_normalized_number_not_blank",
            "ck_pallets_number_not_blank",
            "ck_pallets_version_positive",
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("pallets")
        } == {"uq_pallets_lot_normalized_number"}
        assert {
            constraint["name"] for constraint in inspector.get_foreign_keys("pallets")
        } == {
            "fk_pallets_absorbed_by_user_id_users",
            "fk_pallets_absorbed_into_pallet_id_pallets",
            "fk_pallets_archived_by_user_id_users",
            "fk_pallets_created_by_user_id_users",
            "fk_pallets_lot_id_lots",
            "fk_pallets_updated_by_user_id_users",
        }
        assert connection.execute(
            sa.text("SELECT id, pallet_id FROM boxes ORDER BY id")
        ).all() == [(30, 20), (31, 20)]

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert connection.scalar(
            sa.text("SELECT current_warehouse_id FROM pallets WHERE id = 20")
        ) == 1
        assert next(
            column
            for column in inspector.get_columns("pallets")
            if column["name"] == "current_warehouse_id"
        )["nullable"] is False
        assert {index["name"] for index in inspector.get_indexes("pallets")} == {
            "ix_pallets_absorbed_into",
            "ix_pallets_lot_active",
            "ix_pallets_warehouse_active",
        }
        assert {
            constraint["name"] for constraint in inspector.get_foreign_keys("pallets")
        } == {
            "fk_pallets_absorbed_by_user_id_users",
            "fk_pallets_absorbed_into_pallet_id_pallets",
            "fk_pallets_archived_by_user_id_users",
            "fk_pallets_created_by_user_id_users",
            "fk_pallets_current_warehouse_id_warehouses",
            "fk_pallets_lot_id_lots",
            "fk_pallets_updated_by_user_id_users",
        }
        assert connection.execute(
            sa.text("SELECT id, pallet_id FROM boxes ORDER BY id")
        ).all() == [(30, 20), (31, 20)]
    engine.dispose()


@pytest.mark.parametrize("shape", ["empty", "multi_warehouse"])
def test_0033_sqlite_downgrade_refuses_lossy_shapes(shape: str) -> None:
    migration = _load_migration()
    engine = _sqlite_0032_engine()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO lots (id, name, normalized_name, version) "
                "VALUES (10, 'Unsafe', 'unsafe', 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO pallets "
                "(id, lot_id, current_warehouse_id, pallet_number, "
                "normalized_pallet_number, version, is_active) "
                "VALUES (20, 10, 1, 'P-1', 'p-1', 1, 1)"
            )
        )
        if shape == "multi_warehouse":
            connection.execute(
                sa.text(
                    "INSERT INTO boxes "
                    "(id, box_number, lot_id, current_warehouse_id, status, pallet_id) "
                    "VALUES (30, '001', 10, 1, 'received', 20), "
                    "(31, '002', 10, 2, 'received', 20)"
                )
            )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        with pytest.raises(RuntimeError, match="exactly one warehouse"):
            migration.downgrade()
    engine.dispose()


def test_0033_offline_sql_and_single_head() -> None:
    migration = _load_migration()
    outputs: dict[str, str] = {}
    for dialect in ("postgresql", "sqlite"):
        output = io.StringIO()
        context = MigrationContext.configure(
            dialect_name=dialect,
            opts={"as_sql": True, "output_buffer": output},
        )
        migration.op = Operations(context)
        migration.upgrade()
        migration.downgrade()
        outputs[dialect] = output.getvalue()

    assert "DROP COLUMN current_warehouse_id" in outputs["postgresql"]
    assert "every pallet must have" in outputs["postgresql"]
    assert "UPDATE pallets" in outputs["postgresql"]
    assert "_0033_downgrade_guard" in outputs["sqlite"]
    assert "UPDATE pallets" in outputs["sqlite"]
    for constraint_name in (
        "ck_pallets_archive_state",
        "ck_pallets_absorbed_state",
        "uq_pallets_lot_normalized_number",
        "fk_pallets_absorbed_into_pallet_id_pallets",
        "ix_pallets_lot_active",
        "ix_pallets_absorbed_into",
    ):
        assert constraint_name in outputs["sqlite"]

    root = Path(__file__).parents[1]
    alembic_config = Config(str(root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(root / "alembic"))
    assert ScriptDirectory.from_config(alembic_config).get_heads() == [
        "0034_reduced_email_notifications"
    ]
