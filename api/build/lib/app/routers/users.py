from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.users import User
from app.schemas.users import UserOut, UserUpdate

router = APIRouter(tags=["users"])


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> list[UserOut]:
    rows = db.scalars(select(User).order_by(User.email)).all()
    return [UserOut.model_validate(u) for u in rows]


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_admin)],
) -> UserOut:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if payload.role is not None:
        target.role = payload.role
        target.role_override = True
    if payload.role_override is not None:
        target.role_override = payload.role_override
    if payload.is_active is not None:
        target.is_active = payload.is_active
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)
