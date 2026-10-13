"""Per-request context: request id, access log and HTTP metrics.

The middleware accepts a caller's `X-Request-ID` when it is short and printable,
otherwise it mints one, echoes it on the response, and keeps it in a contextvar so
every log line and every enqueued job for this request can carry it.
"""

from __future__ import annotations

import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.routing import Match, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger, request_id_var, request_state_var
from app.core.metrics import HTTP_DURATION, HTTP_REQUESTS

log = get_logger("app.access")

REQUEST_ID_HEADER = "X-Request-ID"
_ACCEPTED_ID = re.compile(r"^[!-~]{1,128}$")  # visible ASCII only, at most 128 characters


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == b"x-request-id":
            candidate = value.decode("latin-1")
            return candidate if _ACCEPTED_ID.fullmatch(candidate) else None
    return None


def _route_template(scope: Scope) -> str:
    """The matched route's path template, so cardinality stays bounded."""
    router = getattr(scope.get("app"), "router", None)
    for route in getattr(router, "routes", []):
        # Mounts (the MCP app) are not templated routes; they fall through to "unmatched".
        if isinstance(route, Route) and route.matches(scope)[0] == Match.FULL:
            return route.path
    return "unmatched"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or str(uuid.uuid4())
        method = scope.get("method", "")
        route = _route_template(scope)
        # Share one state dict with Request.state so the auth dependency's write is seen here.
        state = scope.setdefault("state", {})
        status_code = 500
        started = time.perf_counter()
        id_token = request_id_var.set(request_id)
        state_token = request_state_var.set(state)

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration = time.perf_counter() - started
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
            HTTP_DURATION.labels(route=route).observe(duration)
            log.info(
                "%s %s %s %.1fms",
                method,
                route,
                status_code,
                duration * 1000,
                extra={
                    "method": method,
                    "route": route,
                    "status": status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
            request_state_var.reset(state_token)
            request_id_var.reset(id_token)


__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware"]
