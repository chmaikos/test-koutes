from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.warehouses import Warehouse


class UserRole(str, enum.Enum):
    admin = "admin"
    warehouse_mover = "warehouse_mover"
    operator = "operator"
    viewer = "viewer"


# Per-warehouse ACL. A row in this table grants its user access to that
# warehouse. Admins ignore the table entirely (they always have access to
# everything); for operators/viewers, an empty set means no access.
user_warehouse_access = Table(
    "user_warehouse_access",
    Base.metadata,
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "warehouse_id",
        Integer,
        ForeignKey("warehouses.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("ix_user_warehouse_access_user_id", "user_id"),
)


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
    # Per-user opt-out: when False, this user is excluded from every alert
    # email recipient list (primary, escalation, and test). Defaults to True
    # so the existing "everyone with ACL gets emails" behaviour is preserved.
    email_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    warehouses: Mapped[list[Warehouse]] = relationship(
        secondary=user_warehouse_access, lazy="selectin"
    )
