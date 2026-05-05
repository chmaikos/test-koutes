"""Per-warehouse access control helpers.

Admins always bypass the ACL. Operators and viewers can only see/act on
warehouses they have an explicit ``user_warehouse_access`` row for. A
non-admin with zero rows sees nothing -- this is what newly created users
get by default.
"""
from __future__ import annotations

from typing import TypeVar

from sqlalchemy import ColumnElement, Select

from app.models.users import User, UserRole

T = TypeVar("T")


def is_admin(user: User) -> bool:
    return user.role == UserRole.admin


def allowed_warehouse_ids(user: User) -> set[int] | None:
    """Return the set of warehouse ids the user may access.

    ``None`` means unrestricted (admin). A (possibly empty) set is returned
    for operators and viewers; an empty set must be treated as "no access at
    all", not "no filter applied".
    """
    if is_admin(user):
        return None
    return {w.id for w in user.warehouses}


def can_access(user: User, warehouse_id: int) -> bool:
    allowed = allowed_warehouse_ids(user)
    if allowed is None:
        return True
    return warehouse_id in allowed


def apply_warehouse_filter(
    stmt: Select[T], user: User, column: ColumnElement[int]
) -> Select[T]:
    """Restrict a SELECT statement to the caller's accessible warehouses.

    Admins get the statement back unchanged. For non-admins we add a
    ``column IN (allowed_ids)`` clause; if the user has no grants we
    deliberately yield an empty result via ``column.in_([])`` so a brand-new
    operator/viewer sees nothing rather than everything.
    """
    allowed = allowed_warehouse_ids(user)
    if allowed is None:
        return stmt
    return stmt.where(column.in_(allowed))
