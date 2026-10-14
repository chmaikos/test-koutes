"""Authentication policy and persisted SSO activity regressions."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException

from app.auth import TokenClaims, validate_access_token
from app.deps import resolve_user_from_token
from app.models.users import User


@pytest.mark.asyncio
async def test_sso_activity_persisted_and_throttled(session, make_user, monkeypatch):
    from app import deps

    user = make_user(oid="active-sso")
    old = datetime.now(UTC) - timedelta(days=1)
    user.last_login_at = old
    session.commit()
    claims = TokenClaims({
        "oid": user.entra_oid, "email": user.email, "name": user.display_name,
        "roles": [user.role.value],
    })

    async def validate(_token):
        return claims

    class Settings:
        auth_configured = True

    monkeypatch.setattr(deps, "get_settings", Settings)
    monkeypatch.setattr(deps, "validate_access_token", validate)
    await resolve_user_from_token(session, "sso-token")
    session.rollback()
    session.expire_all()
    persisted = session.get(User, user.id).last_login_at
    assert persisted.replace(tzinfo=UTC) > old
    await resolve_user_from_token(session, "sso-token")
    session.rollback()
    session.expire_all()
    assert session.get(User, user.id).last_login_at == persisted


@pytest.mark.asyncio
async def test_entra_rejects_header_algorithm_before_jwks(monkeypatch):
    from app import auth

    class Settings:
        auth_configured = True

    monkeypatch.setattr(auth, "get_settings", Settings)

    async def no_fetch(_settings):
        pytest.fail("unsupported algorithm must be rejected before fetching JWKS")

    monkeypatch.setattr(auth, "_fetch_jwks", no_fetch)
    token = jwt.encode(
        {"oid": "attacker"}, "attacker-secret-at-least-32-bytes-long",
        algorithm="HS256", headers={"kid": "key"},
    )
    with pytest.raises(HTTPException) as error:
        await validate_access_token(token)
    assert error.value.status_code == 401
    assert error.value.detail == "unsupported token algorithm"


@pytest.mark.parametrize("claim", ["missing", "0", False, 1])
def test_local_session_version_validation(session, make_user, monkeypatch, claim):
    from app import deps

    user = make_user()
    user.is_local = True
    user.local_session_version = 0
    session.commit()
    claims = {"sub": str(user.id)}
    if claim != "missing":
        claims["session_version"] = claim
    monkeypatch.setattr(deps, "decode_local_token", lambda _: claims)
    if claim == "missing":
        assert deps._resolve_local_user(session, "token") == user
        user.local_session_version = 1
        session.commit()
    with pytest.raises(HTTPException) as error:
        deps._resolve_local_user(session, "token")
    assert error.value.status_code == 401


def test_local_session_migration_backfills_existing_users():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    migration_path = (
        Path(__file__).parents[1] / "alembic/versions/0037_local_session_version.py"
    )
    spec = importlib.util.spec_from_file_location("session_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO users (id) VALUES (1)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.scalar(text("SELECT local_session_version FROM users")) == 0
            column = next(
                col for col in inspect(connection).get_columns("users")
                if col["name"] == "local_session_version"
            )
            assert column["nullable"] is False
            migration.downgrade()
            assert "local_session_version" not in {
                col["name"] for col in inspect(connection).get_columns("users")
            }
    engine.dispose()


@pytest.fixture()
def local_secret(monkeypatch):
    from app.config import get_settings

    secret = "regression-local-secret-at-least-48-bytes-for-all-tested-algorithms"
    monkeypatch.setenv("LOCAL_JWT_SECRET", secret)
    get_settings.cache_clear()
    yield secret
    get_settings.cache_clear()


@pytest.mark.parametrize("missing", ["iss", "sub", "iat", "exp"])
def test_local_token_requires_security_claims(local_secret, missing):
    from app.security import LOCAL_ISSUER, decode_local_token

    now = datetime.now(UTC)
    claims = {"iss": LOCAL_ISSUER, "sub": "1", "iat": now, "exp": now + timedelta(minutes=5)}
    del claims[missing]
    token = jwt.encode(claims, local_secret, algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        decode_local_token(token)
    assert error.value.status_code == 401


def test_local_token_rejects_unsigned_and_wrong_algorithms(local_secret):
    from app.security import LOCAL_ISSUER, decode_local_token, looks_like_local_token

    now = datetime.now(UTC)
    claims = {"iss": LOCAL_ISSUER, "sub": "1", "iat": now, "exp": now + timedelta(minutes=5)}
    for algorithm, key in [("none", None), ("HS384", local_secret)]:
        token = jwt.encode(claims, key, algorithm=algorithm)
        assert looks_like_local_token(token)
        with pytest.raises(HTTPException) as error:
            decode_local_token(token)
        assert error.value.status_code == 401


@pytest.mark.parametrize("invalid", ["signature", "issuer", "expired", "future"])
def test_local_token_rejects_invalid_security_claims(local_secret, invalid):
    from app.security import LOCAL_ISSUER, decode_local_token

    now = datetime.now(UTC)
    claims = {"iss": LOCAL_ISSUER, "sub": "1", "iat": now, "exp": now + timedelta(minutes=5)}
    key = local_secret
    if invalid == "signature":
        key = "untrusted-key-at-least-32-bytes-long"
    elif invalid == "issuer":
        claims["iss"] = "untrusted-issuer"
    elif invalid == "expired":
        claims["exp"] = now - timedelta(seconds=30)
    else:
        claims["iat"] = now + timedelta(minutes=10)
    token = jwt.encode(claims, key, algorithm="HS256")
    with pytest.raises(HTTPException) as error:
        decode_local_token(token)
    assert error.value.status_code == 401


@pytest.fixture()
def entra_signing(monkeypatch):
    import json
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.asymmetric import rsa

    from app import auth

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = "trusted-key"
    settings = SimpleNamespace(
        auth_configured=True, entra_api_audience="api://warehouse", entra_client_id="client-guid",
        issuer="https://trusted/v2.0", issuer_v1="https://trusted/v1",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)

    async def fetch(_settings):
        return [public_jwk]

    monkeypatch.setattr(auth, "_fetch_jwks", fetch)
    return private_key, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_entra_rsa_jwk_issuer_audience_compatibility(entra_signing, version):
    private_key, settings = entra_signing
    claims = {
        "oid": "valid-user", "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iss": settings.issuer if version == "v2" else settings.issuer_v1,
        "aud": settings.entra_api_audience if version == "v2" else settings.entra_client_id,
        "roles": ["operator"],
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "trusted-key"})
    validated = await validate_access_token(token)
    assert validated.oid == "valid-user"
    assert validated.best_role().value == "operator"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_claim", ["iss", "aud", "exp", "oid", "expired", "bad_oid"])
async def test_entra_rejects_missing_or_invalid_security_claims(entra_signing, bad_claim):
    private_key, settings = entra_signing
    claims = {
        "oid": "valid-user", "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iss": settings.issuer, "aud": settings.entra_api_audience,
    }
    if bad_claim == "expired":
        claims["exp"] = datetime.now(UTC) - timedelta(seconds=10)
    elif bad_claim == "bad_oid":
        claims["oid"] = True
    else:
        del claims[bad_claim]
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "trusted-key"})
    with pytest.raises(HTTPException) as error:
        await validate_access_token(token)
    assert error.value.status_code == 401
