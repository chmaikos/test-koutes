"""Local (break-glass) admin authentication tests.

Walk the bootstrap-and-rotate flow end-to-end through the HTTP layer:
provision -> log in with defaults -> blocked from real endpoints -> change
credentials -> log back in with the new ones -> reach gated endpoints.
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.config import get_settings
from app.db import Base
from app.deps import get_db
from app.main import app
from app.models.warehouses import Warehouse
from app.services.local_admin import ensure_local_admin

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "ChangeMe!2026"


@pytest.fixture(autouse=True)
def _override_settings(monkeypatch):
    monkeypatch.setenv("LOCAL_JWT_SECRET", "test-secret-please-rotate")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", DEFAULT_USERNAME)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", DEFAULT_PASSWORD)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def local_client() -> Generator[TestClient, None, None]:
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

    db_module.SessionLocal = SessionTest  # type: ignore[assignment]
    db_module.engine = engine  # type: ignore[assignment]

    seed = SessionTest()
    seed.add_all(
        [
            Warehouse(id=1, name="Building 1", min_inventory=0, max_capacity=500),
            Warehouse(id=2, name="Building 2", min_inventory=0, max_capacity=500),
            Warehouse(id=3, name="Building 3", min_inventory=0, max_capacity=500),
        ]
    )
    seed.commit()
    ensure_local_admin(seed)
    seed.close()

    def _override_db():
        s = SessionTest()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def test_default_admin_provisioned_and_can_login(local_client):
    resp = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["must_change_credentials"] is True
    assert body["user"]["role"] == "admin"
    assert body["user"]["is_local"] is True
    assert body["access_token"]


def test_login_wrong_password_returns_401(local_client):
    resp = _login(local_client, DEFAULT_USERNAME, "totally-wrong")
    assert resp.status_code == 401


def test_login_unknown_user_returns_401_not_404(local_client):
    resp = _login(local_client, "nope", DEFAULT_PASSWORD)
    assert resp.status_code == 401


def test_gated_endpoint_returns_428_until_credentials_changed(local_client):
    token = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}

    me = local_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["must_change_credentials"] is True

    blocked = local_client.get("/api/dashboard/summary", headers=headers)
    assert blocked.status_code == 428
    assert blocked.json()["detail"] == "credentials_must_change"


def test_change_credentials_then_use_new_creds(local_client):
    token = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()[
        "access_token"
    ]
    headers = {"Authorization": f"Bearer {token}"}

    too_short = local_client.post(
        "/api/auth/change-credentials",
        headers=headers,
        json={"current_password": DEFAULT_PASSWORD, "new_password": "short"},
    )
    assert too_short.status_code == 422

    same_password = local_client.post(
        "/api/auth/change-credentials",
        headers=headers,
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": DEFAULT_PASSWORD,
        },
    )
    assert same_password.status_code == 400

    bad_current = local_client.post(
        "/api/auth/change-credentials",
        headers=headers,
        json={
            "current_password": "wrong",
            "new_password": "BrandNewPass!2026",
        },
    )
    assert bad_current.status_code == 401

    ok = local_client.post(
        "/api/auth/change-credentials",
        headers=headers,
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_username": "ops-admin",
            "new_password": "BrandNewPass!2026",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["must_change_credentials"] is False
    assert ok.json()["username"] == "ops-admin"

    # Old credentials no longer work.
    assert _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).status_code == 401

    # New credentials issue a fresh token that reaches gated endpoints.
    new_login = _login(local_client, "ops-admin", "BrandNewPass!2026")
    assert new_login.status_code == 200
    new_body = new_login.json()
    assert new_body["must_change_credentials"] is False
    new_token = new_body["access_token"]

    summary = local_client.get(
        "/api/dashboard/summary",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert summary.status_code == 200, summary.text


def test_bootstrap_is_idempotent_and_does_not_overwrite_changed_password(
    local_client,
):
    """If a restart triggers ensure_local_admin again, an already-rotated
    password must not be reset."""
    token = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()[
        "access_token"
    ]
    local_client.post(
        "/api/auth/change-credentials",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": "BrandNewPass!2026",
        },
    )

    # Simulate a restart re-running the bootstrap.
    s = db_module.SessionLocal()
    try:
        ensure_local_admin(s)
        s.commit()
    finally:
        s.close()

    assert _login(local_client, DEFAULT_USERNAME, "BrandNewPass!2026").status_code == 200
    assert _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).status_code == 401


def test_login_disabled_when_secret_unset(monkeypatch, local_client):
    monkeypatch.setenv("LOCAL_JWT_SECRET", "")
    get_settings.cache_clear()
    resp = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD)
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"]


def test_inactive_local_user_cannot_log_in(local_client):
    s = db_module.SessionLocal()
    from sqlalchemy import select

    from app.models.users import User

    try:
        u = s.scalar(select(User).where(User.username == DEFAULT_USERNAME))
        assert u is not None
        u.is_active = False
        s.commit()
    finally:
        s.close()

    resp = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD)
    assert resp.status_code == 403


def test_now_returns_aware_datetime():
    """Tiny sanity check for the helper used in the auth endpoint."""
    assert datetime.now(UTC).tzinfo is not None
