"""Server-Sent Events stream.

Browsers cannot set custom headers on `EventSource`, so the SPA passes its
access token via the `?access_token=` query param. We validate it through the
same dual-validator (local HS256 + Entra RS256) used elsewhere so the SSE
stream works for both authentication paths.
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.deps import DbSession, resolve_user_from_token
from app.events import bus

router = APIRouter(tags=["stream"])

_KEEPALIVE_INTERVAL_SECONDS = 15


@router.get("/stream")
async def stream(
    request: Request,
    db: DbSession,
    access_token: Annotated[str | None, Query()] = None,
) -> EventSourceResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="missing access_token")
    user = await resolve_user_from_token(db, access_token)
    # Refuse the live stream while the bootstrapped admin still has to rotate
    # credentials, mirroring `require_credentials_set` on the rest of the API.
    if user.must_change_credentials:
        raise HTTPException(
            status_code=428, detail="credentials_must_change"
        )

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
