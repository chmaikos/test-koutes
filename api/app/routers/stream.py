"""Server-Sent Events stream.

Browsers cannot set custom headers on `EventSource`, so the SPA passes its
access token via the `?access_token=` query param. We validate it through the
same dual-validator (local HS256 + Entra RS256) used elsewhere so the SSE
stream works for both authentication paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app import db as db_module
from app.deps import resolve_user_from_token
from app.events import bus
from app.services.acl import allowed_warehouse_ids

logger = logging.getLogger("warehouse.events")

router = APIRouter(tags=["stream"])

_KEEPALIVE_INTERVAL_SECONDS = 15


def _payload_visible_to(payload: str, allowed: set[int] | None) -> bool:
    """Return True when the SSE payload should reach this subscriber.

    Admins (``allowed is None``) see every event. For everyone else we look
    at ``data.warehouse_id`` and only forward events whose warehouse the
    user is permitted to see. We fail closed on payloads that omit
    ``warehouse_id`` -- a non-admin listener should never receive
    cross-warehouse signal that we can't attribute.
    """
    if allowed is None:
        return True
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError):
        logger.warning("dropping unparseable SSE payload")
        return False
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, dict):
        return False
    wid = data.get("warehouse_id")
    if not isinstance(wid, int):
        return False
    return wid in allowed


async def _stream_authorization(access_token: str) -> set[int] | None:
    # Each validation uses a fresh session: never hold DB connections or ORM
    # identity snapshots for the lifetime of the stream.
    db = db_module.SessionLocal()
    try:
        user = await resolve_user_from_token(db, access_token)
        if user.must_change_credentials:
            raise HTTPException(status_code=428, detail="credentials_must_change")
        return allowed_warehouse_ids(user)
    finally:
        db.close()


@router.get("/stream")
async def stream(
    request: Request,
    access_token: Annotated[str | None, Query()] = None,
) -> EventSourceResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="missing access_token")
    await _stream_authorization(access_token)
    queue = await bus.subscribe()

    async def event_generator():
        loop = asyncio.get_running_loop()
        # Renew occasionally even for valid tokens to bound every connection.
        deadline = loop.time() + 300
        try:
            yield {"event": "ready", "data": "{}"}
            while not await request.is_disconnected():
                if loop.time() >= deadline:
                    yield {"event": "reconnect", "data": "{}"}
                    break
                try:
                    payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(_KEEPALIVE_INTERVAL_SECONDS, deadline - loop.time()),
                    )
                except TimeoutError:
                    payload = None
                try:
                    # Re-check expiry, credential version, active status, role
                    # and grants before sending data (or every idle keepalive).
                    allowed = await _stream_authorization(access_token)
                except HTTPException:
                    yield {"event": "reauthenticate", "data": "{}"}
                    break
                if payload is None:
                    yield {"event": "ping", "data": "{}"}
                elif _payload_visible_to(payload, allowed):
                    yield {"event": "message", "data": payload}
        finally:
            await bus.unsubscribe(queue)

    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )
