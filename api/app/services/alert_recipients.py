"""Resolve primary recipients for an alert opening email.

Users with warehouse access and active admins are eligible, subject to
``email_alerts_enabled``. A static fallback covers an empty tenant.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.users import User, UserRole, user_warehouse_access


def _fallback() -> list[str]:
    """Static recipient list configured via ALERT_EMAIL_TO."""
    return list(get_settings().alert_email_to_list)


def _normalise(addresses: list[str]) -> list[str]:
    """Strip empties + dedupe, preserving order so the audit log is stable."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in addresses:
        addr = (raw or "").strip()
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(addr)
    return out


def primary_recipients(db: Session, warehouse_id: int) -> list[str]:
    """Active users who should be notified about ``warehouse_id``.

    The list is the union of:

    * non-admins with an explicit ACL row on this warehouse, and
    * every admin (admins implicitly cover every warehouse).

    Users that are inactive, have no email on file, or have opted out via
    ``email_alerts_enabled = False`` are dropped. Falls back to
    ``ALERT_EMAIL_TO`` when nothing matches so we never silently drop a
    notification on a bare-bones install.
    """
    # Operators/viewers with explicit ACL grant on this warehouse.
    acl_users = db.scalars(
        select(User)
        .join(user_warehouse_access, user_warehouse_access.c.user_id == User.id)
        .where(
            user_warehouse_access.c.warehouse_id == warehouse_id,
            User.is_active.is_(True),
            User.email_alerts_enabled.is_(True),
            User.role.in_((UserRole.operator, UserRole.viewer)),
        )
    ).all()

    # Admins. They bypass the ACL by design, so we union them in regardless
    # of whether they have a row in user_warehouse_access.
    admins = db.scalars(
        select(User).where(
            User.role == UserRole.admin,
            User.is_active.is_(True),
            User.email_alerts_enabled.is_(True),
        )
    ).all()

    addresses = _normalise([u.email for u in acl_users] + [u.email for u in admins])
    if not addresses:
        return _fallback()
    return addresses
