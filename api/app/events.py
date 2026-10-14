"""In-process pub/sub used by the SSE stream.

For <10 concurrent users this trivially fits in a single uvicorn worker. If we
ever need to scale horizontally this is the seam to swap for Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("warehouse.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Register the application loop during lifespan startup/shutdown."""
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        payload = json.dumps({"type": event_type, "data": data}, default=str)
        async with self._lock:
            targets = list(self._subscribers)
        for queue in targets:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("dropping event for slow subscriber: %s", event_type)

    def publish_threadsafe(self, event_type: str, data: dict[str, Any]) -> None:
        """Schedule thread-originated events on the application's live loop."""
        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            logger.debug("event loop unavailable; dropping %s", event_type)
            return
        coroutine = self.publish(event_type, data)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            return

        def report_failure(done):
            if not done.cancelled() and done.exception() is not None:
                logger.error("thread event publication failed", exc_info=done.exception())

        future.add_done_callback(report_failure)


bus = EventBus()


async def publish_notification_event(
    *,
    warehouse_id: int,
    request_id: int,
) -> None:
    """Signal clients to refetch their persisted notification inbox."""
    await bus.publish(
        "notification.created",
        {
            "warehouse_id": warehouse_id,
            "request_id": request_id,
        },
    )


__all__ = ["bus", "publish_notification_event"]
