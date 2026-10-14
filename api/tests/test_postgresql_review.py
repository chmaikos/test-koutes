"""Real PostgreSQL regression checks, isolated from all existing schemas.

Run with TEST_DATABASE_URL pointing to a disposable PostgreSQL database.
Each test creates and drops only its own randomly named schema.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import User, UserRole, Warehouse
from app.models.employees import (
    Employee,
    ProductivityEntry,
    ProductivityEntryEvent,
    ProductivityReportRun,
)
from app.routers.productivity import upsert_entry
from app.schemas.employees import ProductivityEntryCreate
from app.services.barcodes import (
    issue_pending_barcode_identities,
    preserve_pending_barcode_snapshots,
)
from app.services.boxes import create_box
from app.services.lots import (
    _active_lot_use_statement,
    _exclusive_lot_statement,
    _merge_box_lock_statement,
)
from app.services.productivity_dispatch import _claim_run

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured"
)
API_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def postgres_schema():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must reference PostgreSQL")
    schema = "review_test_" + uuid4().hex
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated_url, pool_size=4, max_overflow=0)

    def migrate(revision, *, downgrade=False):
        environment = dict(
            os.environ,
            DATABASE_URL=isolated_url.render_as_string(hide_password=False),
        )
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade" if downgrade else "upgrade", revision],
            cwd=API_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    factory = sessionmaker(bind=engine, autoflush=False)

    @event.listens_for(factory, "before_flush")
    def finalize_barcodes(session, _context, _instances):
        issue_pending_barcode_identities(session, reason="PostgreSQL integration regression")
        preserve_pending_barcode_snapshots(session)

    try:
        yield engine, factory, migrate
    finally:
        engine.dispose()
        # The identifier is generated locally, and cleanup targets only that
        # schema. No pre-existing database objects are removed.
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _seed(factory):
    with factory() as session:
        warehouse = Warehouse(name="Review", min_inventory=0, max_capacity=500)
        user = User(
            entra_oid="review-admin",
            email="review@example.test",
            role=UserRole.admin,
            display_name="Review",
            is_active=True,
            is_local=False,
        )
        session.add_all([warehouse, user])
        session.flush()
        employee = Employee(warehouse_id=warehouse.id, full_name="Review employee")
        session.add(employee)
        session.commit()
        return warehouse.id, user.id, employee.id


def test_concurrent_productivity_upserts_serialize_without_unique_failure(postgres_schema):
    engine, factory, migrate = postgres_schema
    migrate("head")
    warehouse_id, user_id, employee_id = _seed(factory)
    barrier = Barrier(2)

    def write(pages):
        with factory() as session:
            user = session.get(User, user_id)
            barrier.wait(timeout=10)
            return upsert_entry(
                ProductivityEntryCreate(
                    employee_id=employee_id,
                    entry_date=date(2026, 10, 14),
                    pages=pages,
                    hours_worked=8,
                ),
                session,
                user,
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, pages) for pages in (100, 200)]
        ids = [future.result(timeout=20) for future in futures]
    assert ids[0] == ids[1]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProductivityEntry)) == 1
        entry = session.get(ProductivityEntry, ids[0])
        assert entry.warehouse_id == warehouse_id
        events = session.scalars(
            select(ProductivityEntryEvent).order_by(ProductivityEntryEvent.id)
        ).all()
        assert [item.event_type for item in events] == ["created", "corrected"]
        assert events[1].old_values["pages"] != events[1].new_values["pages"]
        assert events[1].new_values["pages"] == entry.pages


def test_concurrent_report_claim_has_exactly_one_owner(postgres_schema):
    _engine, factory, migrate = postgres_schema
    migrate("head")
    warehouse_id, _user_id, _employee_id = _seed(factory)
    barrier = Barrier(2)
    now = datetime.now(UTC)
    report_date = date(2026, 10, 14)

    def claim():
        with factory() as session:
            barrier.wait(timeout=10)
            return _claim_run(session, warehouse_id=warehouse_id, report_date=report_date, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        tokens = [future.result(timeout=20) for future in futures]
    winners = [token for token in tokens if token is not None]
    assert len(winners) == 1
    with factory() as session:
        run = session.get(ProductivityReportRun, (warehouse_id, report_date))
        assert run.claim_token == winners[0]
        assert run.attempts == 1


def test_lot_and_box_lock_statements_execute_against_migrated_postgresql(postgres_schema):
    _engine, factory, migrate = postgres_schema
    migrate("head")
    warehouse_id, user_id, _employee_id = _seed(factory)
    with factory() as session:
        user = session.get(User, user_id)
        box = create_box(
            session, user=user, warehouse_id=warehouse_id, box_number="1", lot="Lock test"
        )
        session.commit()
        lot_id, box_id = box.lot_id, box.id
    with factory() as shared, factory() as exclusive:
        assert shared.scalar(_active_lot_use_statement([lot_id])).id == lot_id
        exclusive.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError) as failure:
            exclusive.scalar(_exclusive_lot_statement([lot_id]))
        assert failure.value.orig.sqlstate == "55P03"
        exclusive.rollback()
        shared.rollback()
        assert exclusive.scalar(_exclusive_lot_statement([lot_id])).id == lot_id
        assert exclusive.scalar(_merge_box_lock_statement([lot_id])).id == box_id


def test_review_migrations_backfill_and_downgrade_only_new_schema(postgres_schema):
    engine, _factory, migrate = postgres_schema
    migrate("0036_barcode_registry")
    with engine.begin() as connection:
        user_id = connection.scalar(
            text(
                "INSERT INTO users (entra_oid,email,display_name,role,is_active,is_local,"
                "must_change_credentials) VALUES ('migration-user','migration@example.test',"
                "'Migration','admin',true,false,false) RETURNING id"
            )
        )
        warehouse_id = connection.scalar(
            text(
                "INSERT INTO warehouses (name,min_inventory,max_capacity) "
                "VALUES ('Migration',0,500) RETURNING id"
            )
        )
        connection.execute(
            text(
                "INSERT INTO productivity_report_runs (warehouse_id,report_date,ok,recipients) "
                "VALUES (:warehouse,'2026-10-14',false,'')"
            ),
            {"warehouse": warehouse_id},
        )
    migrate("head")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT local_session_version FROM users WHERE id=:id"), {"id": user_id}
            )
            == 0
        )
        row = connection.execute(
            text(
                "SELECT attempts,claim_token,claimed_at,next_attempt_at "
                "FROM productivity_report_runs WHERE warehouse_id=:id"
            ),
            {"id": warehouse_id},
        ).one()
        assert tuple(row) == (0, None, None, None)
        assert connection.scalar(text("SELECT count(*) FROM productivity_entry_events")) == 0
    migrate("0036_barcode_registry", downgrade=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
        assert connection.scalar(text("SELECT count(*) FROM productivity_report_runs")) == 1
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0036_barcode_registry"
        )
