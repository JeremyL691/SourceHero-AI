"""Pure ASGI middleware that adds baseline security headers to HTTP responses.

Headers added to every HTTP response (unless the endpoint already set one):
- X-Content-Type-Options: nosniff
- Referrer-Policy: no-referrer
- X-Frame-Options: DENY
- Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
  (only for ``application/json`` responses; HTML such as /docs needs its own scripts)
- Strict-Transport-Security: max-age=31536000; includeSubDomains
  (only when ``public_web_url`` starts with ``https://``; the setting is read on
  every request, so runtime overrides of the settings object apply immediately)

The middleware never buffers bodies: body messages are forwarded as they arrive,
so streaming responses (SSE) keep streaming.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from app.core.config import get_settings

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)
JSON_CSP: tuple[bytes, bytes] = (
    b"content-security-policy",
    b"default-src 'none'; frame-ancestors 'none'",
)
HSTS: tuple[bytes, bytes] = (
    b"strict-transport-security",
    b"max-age=31536000; includeSubDomains",
)


class SecurityHeadersMiddleware:
    """Append security headers to the ``http.response.start`` message."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hsts_enabled = get_settings().public_web_url.startswith("https://")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                content_type = next(
                    (value for name, value in headers if name.lower() == b"content-type"),
                    b"",
                )
                additions = list(BASE_HEADERS)
                if content_type.lower().startswith(b"application/json"):
                    additions.append(JSON_CSP)
                if hsts_enabled:
                    additions.append(HSTS)
                headers.extend((name, value) for name, value in additions if name not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
