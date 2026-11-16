"""Pure ASGI middleware: one request id per HTTP request.

Takes a client/proxy ``X-Request-ID`` only when it matches
``request_context.REQUEST_ID_PATTERN``, else generates one; stores it in the
``request_id_var`` ContextVar for the logging filter and echoes it in the
response header. Pure ASGI (not BaseHTTPMiddleware) so the ContextVar is set
in the same task that runs the endpoint and streaming responses are untouched.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..request_context import accept_or_generate, request_id_var


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = accept_or_generate(incoming)
        token = request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
