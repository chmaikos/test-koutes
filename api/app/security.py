"""Local-auth primitives: bcrypt password hashing + HS256 JWTs.

We keep this completely separate from the Entra/JWKS path in `app.auth` so the
two trust roots can't accidentally cross-contaminate. A local-issued JWT carries
`iss="warehouse-local"` and a numeric `sub` (the local users.id); the Entra
path keeps its own RS256 + JWKS validation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import HTTPException, status
from jwt import InvalidTokenError

from app.config import get_settings

LOCAL_ISSUER = "warehouse-local"
LOCAL_ALG = "HS256"
MAX_PASSWORD_BYTES = 72
# A valid cost-12 hash keeps nonexistent/disabled account checks on bcrypt's
# normal expensive path. It is not a usable account credential.
DUMMY_PASSWORD_HASH = "$2b$12$v8jF6xbqSrmyr37n1OqqrOigedHzAi37ZKl5wzFb8xR8mrWE6cGIS"


# --- passwords ---------------------------------------------------------------


def hash_password(plaintext: str) -> str:
    validate_password_length(plaintext)
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def validate_password_length(plaintext: str) -> str:
    if len(plaintext.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("password must be at most 72 UTF-8 bytes")
    return plaintext


def verify_password(plaintext: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        validate_password_length(plaintext)
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- local JWTs --------------------------------------------------------------


class LocalAuthDisabled(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local authentication is disabled (LOCAL_JWT_SECRET not set)",
        )


def _require_secret() -> str:
    secret = get_settings().local_jwt_secret
    if not secret:
        raise LocalAuthDisabled()
    return secret


def issue_local_token(
    user_id: int, role: str, must_change: bool, session_version: int = 0
) -> str:
    settings = get_settings()
    secret = _require_secret()
    now = datetime.now(UTC)
    payload = {
        "iss": LOCAL_ISSUER,
        "sub": str(user_id),
        "role": role,
        "must_change_credentials": must_change,
        "session_version": session_version,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.local_jwt_ttl_minutes)).timestamp()
        ),
    }
    return jwt.encode(payload, secret, algorithm=LOCAL_ALG)


def decode_local_token(token: str) -> dict:
    secret = _require_secret()
    try:
        return jwt.decode(
            token, secret, algorithms=[LOCAL_ALG], issuer=LOCAL_ISSUER,
            options={"require": ["iss", "sub", "iat", "exp"]},
            # Small tolerance for issuance/validation on separate API workers
            # whose clocks differ slightly. Session revocation is immediate.
            leeway=5,
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}"
        ) from exc


def looks_like_local_token(token: str) -> bool:
    """Cheap, unverified peek so we can route a token to the right validator
    without paying for a JWKS fetch on local-issued tokens."""
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except InvalidTokenError:
        return False
    return unverified.get("iss") == LOCAL_ISSUER
