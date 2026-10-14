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
    from app.routers.auth_local import _login_failures

    _login_failures.clear()
    monkeypatch.setenv("LOCAL_JWT_SECRET", "test-secret-please-rotate-at-least-32-bytes")
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


def test_stream_rejects_missing_or_bad_token(local_client):
    # No `?access_token=` -> 401 missing.
    no_token = local_client.get("/api/stream")
    assert no_token.status_code == 401

    # Random garbage -> 401 (could not be parsed as either token type).
    bad = local_client.get("/api/stream", params={"access_token": "garbage"})
    assert bad.status_code == 401


def test_stream_blocks_on_must_change_credentials(local_client):
    token = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()[
        "access_token"
    ]
    blocked = local_client.get(
        "/api/stream", params={"access_token": token}
    )
    assert blocked.status_code == 428


def test_resolve_user_from_token_accepts_local_token(local_client):
    """Unit-level proof that the same helper used by /api/stream now resolves
    a local-issued HS256 token to its user (this is the regression behind the
    SSE 401s when running behind a reverse proxy with the local admin)."""
    import asyncio

    from app import db as db_module
    from app.deps import resolve_user_from_token

    # Get a fresh, post-rotation local token via the public login flow.
    token0 = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()[
        "access_token"
    ]
    rotated = local_client.post(
        "/api/auth/change-credentials",
        headers={"Authorization": f"Bearer {token0}"},
        json={
            "current_password": DEFAULT_PASSWORD,
            "new_password": "BrandNewPass!2026",
        },
    )
    assert rotated.status_code == 200, rotated.text
    fresh_token = _login(
        local_client, DEFAULT_USERNAME, "BrandNewPass!2026"
    ).json()["access_token"]

    db = db_module.SessionLocal()
    try:
        user = asyncio.run(resolve_user_from_token(db, fresh_token))
    finally:
        db.close()
    assert user.is_local is True
    assert user.role.value == "admin"
    assert user.must_change_credentials is False


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
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid username or password"


def test_rotation_revokes_old_token_and_returns_working_replacement(local_client):
    old_token = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()["access_token"]
    changed = local_client.post(
        "/api/auth/change-credentials",
        headers={"Authorization": f"Bearer {old_token}"},
        json={"current_password": DEFAULT_PASSWORD, "new_password": "Replacement!2026"},
    )
    assert changed.status_code == 200
    replacement = changed.headers["X-Local-Access-Token"]
    assert local_client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {old_token}"}
    ).status_code == 401
    # The stream shares the validator, so its reconnect also rejects the token.
    assert local_client.get(
        "/api/stream", params={"access_token": old_token}
    ).status_code == 401
    assert local_client.get(
        "/api/dashboard/summary", headers={"Authorization": f"Bearer {replacement}"}
    ).status_code == 200


@pytest.mark.parametrize("password", ["a" * 73, "é" * 37])
def test_password_byte_limit_rejected_on_login_and_rotation(local_client, password):
    assert _login(local_client, DEFAULT_USERNAME, password).status_code == 422
    token = _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).json()["access_token"]
    for field in ("current_password", "new_password"):
        payload = {"current_password": DEFAULT_PASSWORD, "new_password": "Replacement!2026"}
        payload[field] = password
        assert local_client.post(
            "/api/auth/change-credentials",
            headers={"Authorization": f"Bearer {token}"}, json=payload,
        ).status_code == 422


def test_bcrypt_byte_limit_and_dummy_hash():
    from app.security import DUMMY_PASSWORD_HASH, hash_password, verify_password

    assert verify_password("warehouse-dummy-timing-check", DUMMY_PASSWORD_HASH)
    boundary = "é" * 36
    hashed = hash_password(boundary)
    assert verify_password(boundary, hashed)
    assert not verify_password(boundary + "suffix", hashed)
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        hash_password(boundary + "suffix")


def test_unknown_and_disabled_users_verify_valid_dummy_hash(local_client, monkeypatch):
    from sqlalchemy import select

    from app.models.users import User
    from app.routers import auth_local
    from app.security import DUMMY_PASSWORD_HASH

    calls = []
    monkeypatch.setattr(
        auth_local, "verify_password", lambda password, hashed: calls.append(hashed)
    )
    assert _login(local_client, "unknown", DEFAULT_PASSWORD).status_code == 401
    with db_module.SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == DEFAULT_USERNAME))
        user.is_active = False
        db.commit()
    assert _login(local_client, DEFAULT_USERNAME, DEFAULT_PASSWORD).status_code == 401
    assert calls == [DUMMY_PASSWORD_HASH, DUMMY_PASSWORD_HASH]


def test_failed_login_rate_limit(local_client, monkeypatch):
    from app.routers import auth_local

    monkeypatch.setattr(auth_local, "verify_password", lambda *_: False)
    for _ in range(auth_local._LOGIN_MAX_FAILURES):
        assert _login(local_client, "unknown", DEFAULT_PASSWORD).status_code == 401
    limited = _login(local_client, "unknown", DEFAULT_PASSWORD)
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["Retry-After"]) <= 61


def test_login_rate_limit_expires_and_bounds_memory(monkeypatch):
    from app.routers import auth_local

    clock = [0.0]
    monkeypatch.setattr(auth_local, "monotonic", lambda: clock[0])
    monkeypatch.setattr(auth_local, "_LOGIN_MAX_PEERS", 2)
    for _ in range(auth_local._LOGIN_MAX_FAILURES):
        auth_local._record_login_failure("peer")
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        auth_local._check_login_rate("peer")
    clock[0] = 61.0
    auth_local._check_login_rate("peer")
    for peer in ("a", "b", "c"):
        auth_local._record_login_failure(peer)
    assert list(auth_local._login_failures) == ["b", "c"]


def test_now_returns_aware_datetime():
    """Tiny sanity check for the helper used in the auth endpoint."""
    assert datetime.now(UTC).tzinfo is not None
