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

from app.db import SessionLocal
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


@router.get("/stream")
async def stream(
    request: Request,
    access_token: Annotated[str | None, Query()] = None,
) -> EventSourceResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="missing access_token")

    # Open a short-lived session purely for auth + ACL snapshot. We must
    # NOT take ``DbSession`` as a FastAPI dependency here: ``yield``-based
    # dependencies are only torn down once the response is fully sent,
    # and an SSE response stays open for the entire lifetime of the
    # browser tab. Holding the connection that long quickly drains the
    # pool (one slot per open tab) and starves every other handler. By
    # closing the session before returning ``EventSourceResponse`` the
    # stream costs zero DB connections while idle.
    db = SessionLocal()
    try:
        user = await resolve_user_from_token(db, access_token)
        # Refuse the live stream while the bootstrapped admin still has to
        # rotate credentials, mirroring `require_credentials_set` on the
        # rest of the API.
        if user.must_change_credentials:
            raise HTTPException(
                status_code=428, detail="credentials_must_change"
            )
        # Snapshot the caller's ACL once at connect time; admins get None
        # (unrestricted), everyone else gets a (possibly empty) set of
        # warehouse ids. Restricted users with no grants will simply
        # receive no events besides the keepalive pings.
        allowed = allowed_warehouse_ids(user)
    finally:
        db.close()

    queue = await bus.subscribe()

    async def event_generator():
        try:
            yield {"event": "ready", "data": "{}"}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=_KEEPALIVE_INTERVAL_SECONDS
                    )
                    if not _payload_visible_to(payload, allowed):
                        continue
                    yield {"event": "message", "data": payload}
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            await bus.unsubscribe(queue)

    # `X-Accel-Buffering: no` discourages outer reverse proxies (nginx, some
    # CDNs) from buffering the SSE response and stalling the live updates.
    return EventSourceResponse(
        event_generator(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
