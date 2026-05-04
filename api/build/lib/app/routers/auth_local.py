"""Break-glass local authentication endpoints.

These run alongside (not instead of) Entra ID SSO. A local user has
`is_local=True` and a username + bcrypt password hash; they receive a JWT
issued by us (HS256, `iss="warehouse-local"`).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models.users import User
from app.schemas.users import UserOut
from app.security import (
    LocalAuthDisabled,
    hash_password,
    issue_local_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 12


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


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


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    """Always-on identity probe: usable by both Entra and local sessions and
    deliberately exempt from the must-change-credentials gate so the SPA can
    decide which screen to render."""
    return UserOut.model_validate(user)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: DbSession) -> LoginResponse:
    settings = get_settings()
    if not settings.local_auth_enabled:
        raise LocalAuthDisabled()

    user = db.scalar(
        select(User).where(
            User.username == payload.username, User.is_local.is_(True)
        )
    )
    # Always go through the bcrypt path even on missing user, so timing
    # leaks don't tell an attacker which usernames exist.
    if user is None:
        verify_password(payload.password, "$2b$12$invalidinvalidinvalidinvalidinvalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user is disabled")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = issue_local_token(
        user_id=user.id,
        role=user.role.value,
        must_change=user.must_change_credentials,
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
) -> UserOut:
    if not user.is_local:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only local users can change credentials here",
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
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)
