"""Tests for SSRF netguard protection and DNS rebinding mitigation."""

from __future__ import annotations

import http.server
import socket
import threading
from dataclasses import dataclass, field

import pytest

from app.core.config import get_settings
from app.ingestion.fetch import FetchError, fetch
from app.ingestion.netguard import (
    NetguardBackend,
    SSRFError,
    validate_url,
)

# The public address the offline test resolver answers with (see tests/conftest.py).
TEST_PUBLIC_IP = "93.184.216.34"


@dataclass
class LocalServer:
    base: str
    hits: list[str] = field(default_factory=list)


@pytest.fixture
def local_server():
    """A real HTTP server on 127.0.0.1; `hits` records every path it was asked for."""
    server_state = LocalServer(base="")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            server_state.hits.append(self.path)
            body = b"private ok" if self.path == "/secret" else b"not found"
            self.send_response(200 if self.path == "/secret" else 404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep test output quiet
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_state.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_state
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/secret",
        "http://10.0.0.1/",
        "http://10.254.0.1:8000/internal",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[fd00:ec2::254]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://100.100.100.200/",
        "http://0.0.0.0:8000/",
    ],
)
def test_direct_private_and_metadata_ips_are_rejected(url: str):
    with pytest.raises((SSRFError, FetchError)) as exc_info:
        validate_url(url)
    err = exc_info.value
    assert getattr(err, "code", None) == "ssrf_blocked"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://127.0.0.1:70",
        "dict://127.0.0.1:2628",
    ],
)
def test_unsupported_schemes_are_rejected(url: str):
    with pytest.raises((SSRFError, FetchError)) as exc_info:
        validate_url(url)
    err = exc_info.value
    assert getattr(err, "code", None) == "unsupported_scheme"


def test_redirect_to_private_ip_is_blocked(respx_mock):
    # Initial request to public host redirects to internal AWS metadata
    respx_mock.get("http://example.com/redirect").respond(
        status_code=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )

    with pytest.raises(FetchError) as exc_info:
        fetch("http://example.com/redirect", allow_robots=False)

    assert exc_info.value.code == "ssrf_blocked"


def test_redirect_to_loopback_is_blocked(respx_mock):
    respx_mock.get("http://example.com/redirect-local").respond(
        status_code=301,
        headers={"Location": "http://127.0.0.1:8787/v1/auth/me"},
    )

    with pytest.raises(FetchError) as exc_info:
        fetch("http://example.com/redirect-local", allow_robots=False)

    assert exc_info.value.code == "ssrf_blocked"


def test_dns_resolution_to_private_ip_is_blocked(monkeypatch):
    import socket

    def mock_getaddrinfo(host, port, *args, **kwargs):
        if host == "rebind.evil.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", port)),
            ]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    with pytest.raises(SSRFError) as exc_info:
        validate_url("http://rebind.evil.com/leak")

    assert exc_info.value.code == "ssrf_blocked"


def test_dns_rebinding_second_lookup_blocked(monkeypatch):
    import socket

    call_count = 0

    def mock_getaddrinfo(host, port, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if host == "rebind-dynamic.test":
            # First lookup returns public, second lookup returns internal
            ip = "93.184.216.34" if call_count == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", mock_getaddrinfo)

    # First lookup passes
    ips = validate_url("http://rebind-dynamic.test/")
    assert ips == ["93.184.216.34"]

    # Second lookup rebinding to loopback is blocked
    with pytest.raises(SSRFError) as exc_info:
        validate_url("http://rebind-dynamic.test/")
    assert exc_info.value.code == "ssrf_blocked"


def test_fetch_allow_private_setting(monkeypatch):
    monkeypatch.setattr(get_settings(), "fetch_allow_private", True)

    # Private IP allowed when flag is enabled
    validate_url("http://127.0.0.1:8000/", allow_private=True)
    validate_url("http://10.0.0.1/", allow_private=True)

    # Cloud metadata is STILL blocked even when private is allowed
    with pytest.raises(SSRFError) as exc_info:
        validate_url("http://169.254.169.254/latest/meta-data/", allow_private=True)
    assert exc_info.value.code == "ssrf_blocked"


@pytest.mark.db
def test_captures_parse_metadata_returns_4xx(make_client):
    client = make_client(email="netguard_user@test.local")
    resp = client.post("/v1/captures/parse", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert resp.status_code in (400, 422)
    data = resp.json()
    assert "error" in data
    # Ensure no raw preview or internal data returned
    assert "preview" not in data or data.get("preview") == ""


@pytest.mark.db
def test_captures_parse_loopback_returns_4xx(make_client):
    client = make_client(email="netguard_user2@test.local")
    resp = client.post("/v1/captures/parse", json={"url": "http://127.0.0.1:8787/v1/auth/me"})
    assert resp.status_code in (400, 422)
    data = resp.json()
    assert "error" in data
    assert "preview" not in data or data.get("preview") == ""


def test_redirect_to_real_private_server_is_refused(respx_mock, local_server):
    """Public hop redirects to a live 127.0.0.1 server: refused, and the server sees nothing."""
    respx_mock.get("http://example.com/start").respond(
        302, headers={"Location": f"{local_server.base}/secret"}
    )

    with pytest.raises(FetchError) as exc_info:
        fetch("http://example.com/start", allow_robots=False)

    assert exc_info.value.code == "ssrf_blocked"
    assert local_server.hits == []


def test_private_fetch_allowed_when_fetch_allow_private(monkeypatch, respx_mock, local_server):
    """With fetch_allow_private on, the same private target is fetched through the real backend."""
    monkeypatch.setattr(get_settings(), "fetch_allow_private", True)
    respx_mock.route(host="127.0.0.1").pass_through()
    respx_mock.get("http://example.com/start").respond(
        302, headers={"Location": f"{local_server.base}/secret"}
    )

    result = fetch("http://example.com/start", allow_robots=False)

    assert result.status == 200
    assert result.content == b"private ok"
    assert result.final_url == f"{local_server.base}/secret"
    assert local_server.hits == ["/secret"]


def test_dns_answer_turning_private_on_second_lookup_is_refused(monkeypatch):
    """Lookup 1 (validation) is public; lookup 2 (connect) is loopback: the real backend refuses."""
    lookups = {"count": 0}

    def resolver(host, port, *args, **kwargs):
        lookups["count"] += 1
        ip = TEST_PUBLIC_IP if lookups["count"] == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    assert validate_url("http://rebind-live.test/") == [TEST_PUBLIC_IP]

    with pytest.raises(SSRFError) as exc_info:
        NetguardBackend(allow_private=False).connect_tcp("rebind-live.test", 80)

    assert exc_info.value.code == "ssrf_blocked"
    assert lookups["count"] == 2
