"""Server-Sent Events stream.

Browsers cannot set custom headers on `EventSource`, so the SPA passes its
access token via the `?access_token=` query param. We validate it the same way
as the Authorization header path.
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.auth import validate_access_token
from app.events import bus

router = APIRouter(tags=["stream"])

_KEEPALIVE_INTERVAL_SECONDS = 15


@router.get("/stream")
async def stream(
    request: Request,
    access_token: Annotated[str | None, Query()] = None,
) -> EventSourceResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="missing access_token")
    await validate_access_token(access_token)

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

    return EventSourceResponse(event_generator())
