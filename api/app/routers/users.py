from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.deps import CurrentUser, DbSession, require_admin
from app.models.users import User
from app.models.warehouses import Warehouse
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
    if payload.email_alerts_enabled is not None:
        target.email_alerts_enabled = payload.email_alerts_enabled
    if payload.warehouse_ids is not None:
        # Replace the user's per-warehouse ACL wholesale. We validate that
        # every requested id refers to a real warehouse so admins get a
        # clear 400 instead of a silent FK error on commit. Admins still
        # accept the field but it has no runtime effect (allowed_warehouse_ids
        # returns None for them); the rows are stored verbatim so demoting
        # them back to operator restores the explicit list.
        requested = sorted(set(payload.warehouse_ids))
        if requested:
            found = db.scalars(
                select(Warehouse).where(Warehouse.id.in_(requested))
            ).all()
            found_ids = {w.id for w in found}
            missing = [wid for wid in requested if wid not in found_ids]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown warehouse_ids: {missing}",
                )
            target.warehouses = list(found)
        else:
            target.warehouses = []
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)
