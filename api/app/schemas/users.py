from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    email_alerts_enabled: bool = True
    email_requests_enabled: bool = True
    last_login_at: datetime | None = None
    username: str | None = None
    is_local: bool = False
    must_change_credentials: bool = False
    warehouse_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _populate_warehouse_ids(cls, data: Any) -> Any:
        """When validating from a SQLAlchemy ``User``, materialise
        ``warehouse_ids`` from the ``warehouses`` relationship.

        ``from_attributes=True`` only reads named fields, so ``warehouse_ids``
        on its own would always fall back to the default (empty list). We
        intercept the ORM instance here, project every field by name, and
        attach the derived id list so the schema stays a single
        ``UserOut.model_validate(user)`` call at the call sites.
        """
        if isinstance(data, dict):
            return data
        warehouses = getattr(data, "warehouses", None)
        if warehouses is None:
            return data
        projected = {
            name: getattr(data, name, None)
            for name in cls.model_fields
            if name != "warehouse_ids"
        }
        projected["warehouse_ids"] = sorted({w.id for w in warehouses})
        return projected


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    role_override: bool | None = None
    email_alerts_enabled: bool | None = None
    email_requests_enabled: bool | None = None
    warehouse_ids: list[int] | None = None


class UserPreferencesUpdate(BaseModel):
    email_requests_enabled: bool
