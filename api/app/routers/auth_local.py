"""Break-glass local authentication endpoints.

These run alongside (not instead of) Entra ID SSO. A local user has
`is_local=True` and a username + bcrypt password hash; they receive a JWT
issued by us (HS256, `iss="warehouse-local"`).
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models.users import User
from app.schemas.users import UserOut
from app.security import (
    DUMMY_PASSWORD_HASH,
    LocalAuthDisabled,
    hash_password,
    issue_local_token,
    validate_password_length,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 12
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_FAILURES = 10
_LOGIN_MAX_PEERS = 4096
_login_failures: OrderedDict[str, tuple[float, int]] = OrderedDict()
_login_lock = Lock()


def _check_login_rate(peer: str) -> None:
    now = monotonic()
    with _login_lock:
        while _login_failures:
            first = next(iter(_login_failures))
            if now - _login_failures[first][0] < _LOGIN_WINDOW_SECONDS:
                break
            _login_failures.pop(first)
        entry = _login_failures.get(peer)
        if entry is not None and entry[1] >= _LOGIN_MAX_FAILURES:
            retry = max(1, int(_LOGIN_WINDOW_SECONDS - (now - entry[0])) + 1)
            raise HTTPException(
                status_code=429, detail="too many failed login attempts",
                headers={"Retry-After": str(retry)},
            )


def _record_login_failure(peer: str) -> None:
    with _login_lock:
        start, count = _login_failures.get(peer, (monotonic(), 0))
        _login_failures[peer] = (start, count + 1)
        if len(_login_failures) > _LOGIN_MAX_PEERS:
            _login_failures.popitem(last=False)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)

    _password_bytes = field_validator("password")(validate_password_length)


class LoginResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: str = "Bearer"
    expires_in_minutes: int
    must_change_credentials: bool
    user: UserOut


class ChangeCredentialsRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=255)
    new_username: str | None = Field(default=None, min_length=1, max_length=64)

    _password_bytes = field_validator("current_password", "new_password")(
        validate_password_length
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """Always-on identity probe: usable by both Entra and local sessions and
    deliberately exempt from the must-change-credentials gate so the SPA can
    decide which screen to render."""
    return UserOut.model_validate(user)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: DbSession, request: Request) -> LoginResponse:
    settings = get_settings()
    if not settings.local_auth_enabled:
        raise LocalAuthDisabled()

    # Process-local defense in depth; deployments with multiple workers should
    # also rate-limit at the trusted reverse proxy. Ignore client-supplied XFF.
    peer = request.client.host if request.client else "unknown"
    _check_login_rate(peer)

    user = db.scalar(
        select(User).where(
            User.username == payload.username, User.is_local.is_(True)
        )
    )
    # Always go through the bcrypt path even on missing user, so timing
    # leaks don't tell an attacker which usernames exist.
    password_ok = verify_password(
        payload.password,
        (user.password_hash or DUMMY_PASSWORD_HASH)
        if user is not None and user.is_active else DUMMY_PASSWORD_HASH,
    )
    if user is None or not user.is_active or not password_ok:
        _record_login_failure(peer)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )

    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)

    token = issue_local_token(
        user_id=user.id,
        role=user.role.value,
        must_change=user.must_change_credentials,
        session_version=user.local_session_version,
    )
    return LoginResponse(
        access_token=token,
        expires_in_minutes=settings.local_jwt_ttl_minutes,
        must_change_credentials=user.must_change_credentials,
        user=UserOut.model_validate(user),
    )


@router.post("/change-credentials", response_model=UserOut)
def change_credentials(
    payload: ChangeCredentialsRequest,
    db: DbSession,
    user: CurrentUser,
    response: Response,
) -> UserOut:
    if not user.is_local:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only local users can change credentials here",
        )
    # Serialize concurrent rotations before checking the current password.
    user = db.scalar(
        select(User).where(User.id == user.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="current password is incorrect",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new password must differ from the current one",
        )

    if payload.new_username is not None:
        new_username = payload.new_username.strip()
        if new_username and new_username != user.username:
            duplicate = db.scalar(
                select(User).where(
                    User.username == new_username, User.id != user.id
                )
            )
            if duplicate is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"username {new_username!r} is already taken",
                )
            user.username = new_username

    user.password_hash = hash_password(payload.new_password)
    user.must_change_credentials = False
    user.local_session_version += 1
    db.commit()
    db.refresh(user)
    response.headers["X-Local-Access-Token"] = issue_local_token(
        user.id, user.role.value, user.must_change_credentials, user.local_session_version
    )
    return UserOut.model_validate(user)
