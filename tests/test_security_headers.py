"""Tests for SecurityHeadersMiddleware (no database required)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security_headers import SecurityHeadersMiddleware

JSON_CSP = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=31536000; includeSubDomains"
SSE_CHUNKS = [b"data: one\n\n", b"data: two\n\n"]


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/json")
    def json_route() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/html")
    def html_route() -> HTMLResponse:
        return HTMLResponse("<html><body>hi</body></html>")

    @app.get("/sse")
    async def sse_route() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            for chunk in SSE_CHUNKS:
                yield chunk

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


def test_json_response_has_all_security_headers(client: TestClient) -> None:
    resp = client.get("/json")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["content-security-policy"] == JSON_CSP


def test_html_response_has_no_csp(client: TestClient) -> None:
    resp = client.get("/html")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in resp.headers


def test_streaming_response_still_streams_and_has_nosniff(client: TestClient) -> None:
    with client.stream("GET", "/sse") as resp:
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "content-security-policy" not in resp.headers
        chunks = list(resp.iter_bytes())
    assert b"".join(chunks) == b"".join(SSE_CHUNKS)


def test_middleware_forwards_body_chunks_without_buffering() -> None:
    events: list[str] = []

    async def app(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        events.append("app-resumed-after-first-chunk")
        await send({"type": "http.response.body", "body": b"second", "more_body": False})

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    async def send(message) -> None:  # type: ignore[no-untyped-def]
        events.append(f"{message['type']}:{message.get('body', b'')!r}")

    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    asyncio.run(SecurityHeadersMiddleware(app)(scope, receive, send))

    assert events == [
        "http.response.start:b''",
        "http.response.body:b'first'",
        "app-resumed-after-first-chunk",
        "http.response.body:b'second'",
    ]


def test_hsts_added_for_https_public_url(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_web_url", "https://app.sourcehero.test")
    resp = client.get("/json")
    assert resp.headers["strict-transport-security"] == HSTS
    assert resp.headers["content-security-policy"] == JSON_CSP


def test_no_hsts_for_http_public_url(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_web_url", "http://127.0.0.1:3787")
    resp = client.get("/json")
    assert resp.status_code == 200
    assert "strict-transport-security" not in resp.headers
    assert resp.headers["x-frame-options"] == "DENY"


def test_existing_endpoint_header_is_not_overwritten() -> None:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/custom")
    def custom() -> JSONResponse:
        return JSONResponse({"ok": True}, headers={"X-Frame-Options": "SAMEORIGIN"})

    resp = TestClient(app).get("/custom")
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["x-content-type-options"] == "nosniff"
