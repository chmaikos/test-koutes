"""Local-auth primitives: bcrypt password hashing + HS256 JWTs.

We keep this completely separate from the Entra/JWKS path in `app.auth` so the
two trust roots can't accidentally cross-contaminate. A local-issued JWT carries
`iss="warehouse-local"` and a numeric `sub` (the local users.id); the Entra
path keeps its own RS256 + JWKS validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings

LOCAL_ISSUER = "warehouse-local"
LOCAL_ALG = "HS256"


# --- passwords ---------------------------------------------------------------


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
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


def issue_local_token(user_id: int, role: str, must_change: bool) -> str:
    settings = get_settings()
    secret = _require_secret()
    now = datetime.now(timezone.utc)
    payload = {
        "iss": LOCAL_ISSUER,
        "sub": str(user_id),
        "role": role,
        "must_change_credentials": must_change,
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
            token, secret, algorithms=[LOCAL_ALG], issuer=LOCAL_ISSUER
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"invalid token: {exc}"
        ) from exc


def looks_like_local_token(token: str) -> bool:
    """Cheap, unverified peek so we can route a token to the right validator
    without paying for a JWKS fetch on local-issued tokens."""
    try:
        unverified = jwt.get_unverified_claims(token)
    except JWTError:
        return False
    return unverified.get("iss") == LOCAL_ISSUER
