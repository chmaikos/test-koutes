"""Bootstrap helpers for the local (break-glass) admin user.

Idempotent: if the configured username already exists we leave it alone (so a
restart doesn't reset the password the user picked). If no local admin exists
at all we provision one with the configured defaults and force a credential
change on first login.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.users import User, UserRole
from app.security import hash_password

logger = logging.getLogger("warehouse.bootstrap")


def ensure_local_admin(db: Session) -> User | None:
    """Make sure there is at least one active local admin in the database."""
    settings = get_settings()
    existing_local_admin = db.scalar(
        select(User).where(
            User.is_local.is_(True),
            User.role == UserRole.admin,
            User.is_active.is_(True),
        )
    )
    if existing_local_admin is not None:
        return existing_local_admin

    # No active local admin: provision one with the configured defaults.
    username = settings.bootstrap_admin_username.strip()
    if not username:
        logger.warning("BOOTSTRAP_ADMIN_USERNAME is empty; skipping local admin provisioning")
        return None

    duplicate = db.scalar(select(User).where(User.username == username))
    if duplicate is not None:
        # Username taken by an inactive/non-admin local user; promote it.
        duplicate.is_local = True
        duplicate.role = UserRole.admin
        duplicate.role_override = True
        duplicate.is_active = True
        duplicate.must_change_credentials = True
        duplicate.password_hash = hash_password(settings.bootstrap_admin_password)
        db.commit()
        db.refresh(duplicate)
        logger.warning(
            "promoted existing user %r to local admin and reset credentials",
            username,
        )
        return duplicate

    user = User(
        entra_oid=None,
        email="",
        display_name="Local Admin",
        role=UserRole.admin,
        role_override=True,
        is_active=True,
        username=username,
        password_hash=hash_password(settings.bootstrap_admin_password),
        is_local=True,
        must_change_credentials=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.warning(
        "provisioned default local admin %r - CHANGE THESE CREDENTIALS ON FIRST LOGIN",
        username,
    )
    return user
