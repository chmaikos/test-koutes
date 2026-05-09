"""Recipient-resolver tests.

Cover the routing rules in :mod:`app.services.alert_recipients`:

* per-warehouse ACL drives the primary list (operators + viewers),
* admins are unioned in regardless of ACL,
* opt-out via ``email_alerts_enabled`` removes a user even when their ACL
  says they should be on the list,
* inactive users never appear,
* ALERT_EMAIL_TO is the final fallback when the resolved list is empty,
* escalation_recipients only contains active admins.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.models.users import UserRole
from app.models.warehouses import Warehouse
from app.services.alert_recipients import (
    escalation_recipients,
    primary_recipients,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache(monkeypatch):
    """Settings are LRU-cached; clear before/after each test so envvar
    overrides actually take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_user(
    session,
    *,
    role: UserRole = UserRole.operator,
    email: str,
    active: bool = True,
    opt_in: bool = True,
    warehouses: list[int] | None = None,
):
    from app.models.users import User

    u = User(
        entra_oid=f"oid-{email}",
        email=email,
        display_name=email,
        role=role,
        is_active=active,
        is_local=False,
        must_change_credentials=False,
        email_alerts_enabled=opt_in,
    )
    if warehouses:
        u.warehouses = [session.get(Warehouse, w) for w in warehouses]
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_primary_includes_acl_users_and_admins(session, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "")
    get_settings.cache_clear()

    _make_user(session, role=UserRole.admin, email="boss@example.com")
    _make_user(
        session, role=UserRole.operator, email="op2@example.com", warehouses=[2]
    )
    _make_user(
        session, role=UserRole.operator, email="op1@example.com", warehouses=[1]
    )
    # Should NOT show up: ACL is on a different warehouse.
    _make_user(
        session,
        role=UserRole.operator,
        email="elsewhere@example.com",
        warehouses=[3],
    )

    result = primary_recipients(session, warehouse_id=2)
    # op2 (ACL match) + boss (admin), no op1 / elsewhere.
    assert "op2@example.com" in result
    assert "boss@example.com" in result
    assert "op1@example.com" not in result
    assert "elsewhere@example.com" not in result


def test_primary_skips_opt_out_and_inactive_users(session, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "")
    get_settings.cache_clear()

    _make_user(
        session,
        role=UserRole.operator,
        email="active@example.com",
        warehouses=[1],
    )
    _make_user(
        session,
        role=UserRole.operator,
        email="opted-out@example.com",
        warehouses=[1],
        opt_in=False,
    )
    _make_user(
        session,
        role=UserRole.operator,
        email="disabled@example.com",
        warehouses=[1],
        active=False,
    )
    _make_user(
        session,
        role=UserRole.admin,
        email="admin-out@example.com",
        opt_in=False,
    )

    result = primary_recipients(session, warehouse_id=1)
    assert result == ["active@example.com"]


def test_primary_falls_back_to_alert_email_to_when_empty(session, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.com,oncall@example.com")
    get_settings.cache_clear()

    # No users created at all -> fallback should kick in.
    assert primary_recipients(session, warehouse_id=1) == [
        "ops@example.com",
        "oncall@example.com",
    ]


def test_primary_dedupes_case_insensitively(session, monkeypatch):
    """A user with ACL who is also an admin shouldn't be listed twice."""
    monkeypatch.setenv("ALERT_EMAIL_TO", "")
    get_settings.cache_clear()

    _make_user(
        session,
        role=UserRole.admin,
        email="Boss@Example.com",
        warehouses=[1],
    )
    result = primary_recipients(session, warehouse_id=1)
    assert result == ["Boss@Example.com"]


def test_escalation_admins_only(session, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "")
    get_settings.cache_clear()

    _make_user(session, role=UserRole.admin, email="boss@example.com")
    _make_user(session, role=UserRole.admin, email="boss2@example.com")
    _make_user(
        session,
        role=UserRole.operator,
        email="op@example.com",
        warehouses=[1, 2, 3],
    )

    result = escalation_recipients(session)
    assert set(result) == {"boss@example.com", "boss2@example.com"}


def test_escalation_falls_back_when_no_admins(session, monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "leadership@example.com")
    get_settings.cache_clear()

    _make_user(
        session,
        role=UserRole.operator,
        email="op@example.com",
        warehouses=[1, 2],
    )

    assert escalation_recipients(session) == ["leadership@example.com"]
