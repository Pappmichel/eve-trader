"""Pure ASGI middleware: cap the request body of ``/api/*`` at 1 MiB.

The reverse proxy already caps bodies; this is the application-side limit, so
the app is bounded without the proxy too. A declared ``Content-Length`` above
the limit is refused before the body is read; otherwise the bytes are counted
as they stream in (chunked uploads have no length), and the request is refused
as soon as the count passes the limit. Pure ASGI rather than
``BaseHTTPMiddleware`` so the body stays streamed and a refused request never
buffers more than the limit.

Registered inside ``RequestIdMiddleware`` (so the 413 carries ``X-Request-ID``)
and outside ``AccessGateMiddleware`` (an oversized body is refused before any
session lookup).
"""
from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 1024 * 1024  # 1 MiB
_BODY = json.dumps({"detail": "request_too_large"}).encode()


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.max_bytes:
                    await self._refuse(send)
                    return
                break

        received = 0
        too_large = False
        refused = False

        async def counting_receive() -> Message:
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Never hand the app a truncated body (it could still be
                    # valid JSON followed by padding): tell it the client is
                    # gone and answer 413 ourselves.
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal refused
            if too_large:
                # Whatever the app answers to a body it never got is replaced.
                if not refused:
                    refused = True
                    await self._refuse(send)
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not too_large:
                raise
        if too_large and not refused:
            await self._refuse(send)

    async def _refuse(self, send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_BODY)).encode()),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": _BODY})
