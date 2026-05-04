"""FastAPI dependencies: DB session, current user, role gates."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TokenClaims, validate_access_token
from app.db import get_db
from app.models.users import User, UserRole
from app.security import decode_local_token, looks_like_local_token

DbSession = Annotated[Session, Depends(get_db)]


async def _extract_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def _resolve_local_user(db: Session, token: str) -> User:
    claims = decode_local_token(token)
    sub = claims.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid local token"
        ) from exc
    user = db.get(User, user_id)
    if user is None or not user.is_local:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="local user not found"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="user is disabled"
        )
    return user


async def get_current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    token = await _extract_token(authorization)

    # Local-issued JWT? Validate with our HS256 secret, no JWKS round-trip.
    if looks_like_local_token(token):
        return _resolve_local_user(db, token)

    claims: TokenClaims = await validate_access_token(token)
    if not claims.oid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="oid missing")

    user = db.scalar(select(User).where(User.entra_oid == claims.oid))
    role_from_token = claims.best_role()
    now = datetime.now(UTC)
    if user is None:
        user = User(
            entra_oid=claims.oid,
            email=claims.email,
            display_name=claims.name,
            role=role_from_token,
            is_active=True,
            last_login_at=now,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        changed = False
        if user.email != claims.email and claims.email:
            user.email = claims.email
            changed = True
        if user.display_name != claims.name and claims.name:
            user.display_name = claims.name
            changed = True
        # Token role wins unless an admin pinned an override locally.
        if not user.role_override and user.role != role_from_token:
            user.role = role_from_token
            changed = True
        user.last_login_at = now
        if changed:
            db.commit()
            db.refresh(user)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="user is disabled"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole):
    role_values = {r for r in roles}

    def _checker(user: CurrentUser) -> User:
        if user.role not in role_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires one of: {[r.value for r in role_values]}",
            )
        return user

    return _checker


require_admin = require_role(UserRole.admin)
require_operator = require_role(UserRole.admin, UserRole.operator)


def require_credentials_set(user: CurrentUser) -> User:
    """Block access to most of the app until a freshly-bootstrapped local
    admin has chosen their own username + password (HTTP 428 Precondition
    Required so the SPA can branch unambiguously)."""
    if user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="credentials_must_change",
        )
    return user
