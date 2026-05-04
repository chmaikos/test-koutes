from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.users import UserRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entra_oid: str | None
    email: str
    display_name: str
    role: UserRole
    role_override: bool
    is_active: bool
    last_login_at: datetime | None = None
    username: str | None = None
    is_local: bool = False
    must_change_credentials: bool = False


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    role_override: bool | None = None
