"""Test fixtures: spin up the FastAPI app against an in-memory SQLite DB and
override the auth dependency so we can hit endpoints without a real Entra token.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.db import Base
from app.deps import get_current_user, get_db
from app.main import app
from app.models.users import User, UserRole
from app.models.warehouses import Warehouse
from app.services.barcodes import (
    issue_pending_barcode_identities,
    preserve_pending_barcode_snapshots,
)


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_pragma_on_connect(dbapi_conn, _conn_record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    SessionTest = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    @event.listens_for(SessionTest, "before_flush")
    def _issue_test_barcodes(test_session, _flush_context, _instances):
        # Production creation paths will adopt the explicit primitive in the
        # lifecycle todo. Tests get the same primitive through their isolated
        # session so legacy direct model construction remains concise.
        issue_pending_barcode_identities(test_session, reason="test fixture issuance")
        preserve_pending_barcode_snapshots(test_session)

    s = SessionTest()
    s.add_all(
        [
            Warehouse(id=1, name="Building 1", min_inventory=0, max_capacity=500),
            Warehouse(id=2, name="Building 2", min_inventory=0, max_capacity=500),
            Warehouse(id=3, name="Building 3", min_inventory=0, max_capacity=500),
        ]
    )
    s.commit()

    # Keep the same session bound to the engine so the API uses our DB.
    db_module.SessionLocal = SessionTest  # type: ignore[assignment]
    db_module.engine = engine  # type: ignore[assignment]

    try:
        yield s
    finally:
        s.close()
        # SQLite cannot drop a table while rows reference that same table.
        # Production uses PostgreSQL, which drops the named self-FK first.
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def make_user(session: Session):
    counter = {"n": 0}

    def _make(role: UserRole = UserRole.operator, *, oid: str | None = None) -> User:
        counter["n"] += 1
        u = User(
            entra_oid=oid or f"oid-{counter['n']}",
            email=f"user{counter['n']}@example.com",
            display_name=f"User {counter['n']}",
            role=role,
            is_active=True,
            is_local=False,
            must_change_credentials=False,
            last_login_at=datetime.now(UTC),
        )
        # Mirror the production migration's CROSS JOIN backfill: every
        # operator/viewer fixture starts with access to all currently
        # seeded warehouses, so existing tests (which assume non-admins
        # can act on any of buildings 1/2/3) keep working untouched.
        # Admins ignore the table entirely, so we leave them empty.
        if role != UserRole.admin:
            u.warehouses = list(session.scalars(select(Warehouse)).all())
        session.add(u)
        session.commit()
        session.refresh(u)
        return u

    return _make


@pytest.fixture()
def client(session: Session, make_user) -> Generator[TestClient, None, None]:
    admin = make_user(UserRole.admin)

    def _override_user() -> User:
        return admin

    def _override_db() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
