from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Entra OID is unique among SSO users; null for local (break-glass) accounts.
    entra_oid: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True, default="")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.viewer, nullable=False
    )
    role_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Local (break-glass) auth: only populated when is_local=True.
    username: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_credentials: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
