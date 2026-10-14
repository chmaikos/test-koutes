"""Prevent shared HTTP caches from retaining authenticated API responses."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PrivateApiResponses:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store, max-age=0"
                headers.add_vary_header("Authorization")
            await send(message)

        # Inspect headers only; never buffer SSE or binary response bodies.
        await self.app(scope, receive, send_private)
