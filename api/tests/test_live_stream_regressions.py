from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi import HTTPException

from app.events import EventBus
from app.main import _RedactStreamToken
from app.models.users import User, UserRole
from app.routers import stream as stream_module


def test_thread_publication_reaches_application_loop():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        queue = await bus.subscribe()
        await asyncio.to_thread(bus.publish_threadsafe, "alert.triggered", {"warehouse_id": 1})
        message = json.loads(await asyncio.wait_for(queue.get(), timeout=1))
        assert message == {"type": "alert.triggered", "data": {"warehouse_id": 1}}
        await bus.unsubscribe(queue)
        bus.bind_loop(None)

    asyncio.run(scenario())


class ConnectedRequest:
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize("revocation", ["inactive", "expired", "credentials"])
def test_stream_revalidates_before_delivering_events(session, make_user, monkeypatch, revocation):
    user = make_user(UserRole.viewer)
    user_id = user.id
    reject_token = False

    async def validate(db, token):
        resolved = db.get(User, user_id)
        if reject_token or not resolved.is_active:
            raise HTTPException(status_code=401)
        return resolved

    monkeypatch.setattr(stream_module, "resolve_user_from_token", validate)

    async def scenario():
        bus = EventBus()
        monkeypatch.setattr(stream_module, "bus", bus)
        response = await stream_module.stream(ConnectedRequest(), "token")
        iterator = response.body_iterator
        assert (await anext(iterator))["event"] == "ready"
        if revocation == "inactive":
            user.is_active = False
        elif revocation == "credentials":
            user.must_change_credentials = True
        session.commit()
        await bus.publish("alert.triggered", {"warehouse_id": 1})
        assert (await anext(iterator))["event"] == "reauthenticate"
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
        assert not bus._subscribers

    if revocation == "expired":
        # Let initial authentication succeed, then reject the same token on recheck.
        original_validate = validate
        calls = 0

        async def expire_after_connect(db, token):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise HTTPException(status_code=401)
            return await original_validate(db, token)

        monkeypatch.setattr(stream_module, "resolve_user_from_token", expire_after_connect)
    asyncio.run(scenario())


def test_stream_refreshes_warehouse_acl_on_live_connection(session, make_user, monkeypatch):
    user = make_user(UserRole.viewer)
    user_id = user.id

    async def validate(db, token):
        return db.get(User, user_id)

    monkeypatch.setattr(stream_module, "resolve_user_from_token", validate)

    async def scenario():
        bus = EventBus()
        monkeypatch.setattr(stream_module, "bus", bus)
        response = await stream_module.stream(ConnectedRequest(), "token")
        iterator = response.body_iterator
        assert (await anext(iterator))["event"] == "ready"
        user.warehouses = [warehouse for warehouse in user.warehouses if warehouse.id == 2]
        session.commit()
        await bus.publish("alert.triggered", {"warehouse_id": 1})
        await bus.publish("alert.triggered", {"warehouse_id": 2})
        delivered = await asyncio.wait_for(anext(iterator), timeout=1)
        assert json.loads(delivered["data"])["data"]["warehouse_id"] == 2
        await iterator.aclose()
        assert not bus._subscribers

    asyncio.run(scenario())


def test_uvicorn_access_log_redacts_stream_query():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("client", "GET", "/api/stream?access_token=secret", "1.1", 200),
        None,
    )
    assert _RedactStreamToken().filter(record)
    assert "secret" not in record.getMessage()
    assert "/api/stream" in record.getMessage()
