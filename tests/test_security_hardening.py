"""Regression tests for H1.4 security hardening (SEC-7, SEC-9, SEC-10, SEC-11)."""

from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.api.deps import CurrentUser
from app.mcp import server as mcp_server


def test_upload_chunked_size_limit_aborts_at_413(make_client):
    """SEC-7: Uploading data larger than MAX_UPLOAD_BYTES aborts with 413 without reading all."""
    client = make_client(email="uploader@test.local")
    # 26 MB of bytes
    large_data = b"a" * (26 * 1024 * 1024)
    files = {"file": ("test.txt", io.BytesIO(large_data), "text/plain")}
    resp = client.post("/v1/sources/upload", files=files)
    assert resp.status_code == 413, resp.text
    body = resp.json()
    assert body["error"]["code"] == "file_too_large"


@pytest.mark.db
def test_readyz_degraded_returns_503_and_sanitizes_detail(client):
    """SEC-9: When DB fails, /readyz returns 503 and checks['database'] does not leak credentials or raw exceptions."""
    with patch("app.core.db.get_engine") as mock_engine:
        mock_conn = MagicMock()
        mock_conn.__enter__.side_effect = Exception(
            "psycopg.OperationalError: connection to server at 'postgres.secret-internal.net' (10.0.0.1) failed"
        )
        mock_engine.return_value.connect.return_value = mock_conn

        resp = client.get("/readyz")
        assert resp.status_code == 503, resp.text
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["checks"]["database"] == "error"
        assert "secret-internal" not in str(data)


@pytest.mark.db
def test_oauth_callback_handles_unexpected_error_without_500(client, db):
    """SEC-10: OAuth callback handles exceptions without .code gracefully, redirecting with reason."""
    with patch(
        "app.llm.credentials.oauth_pkce.complete_authorization",
        side_effect=ValueError("unexpected parsing error"),
    ):
        resp = client.get(
            "/v1/settings/llm/oauth/callback?code=fake_code&state=fake_state", follow_redirects=False
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "oauth=error" in loc
        assert "reason=oauth_failed" in loc or "reason=failed" in loc


def test_mcp_error_sanitization():
    """SEC-11: MCP tool errors are sanitized into {'error': code, 'message': text} without raw tracebacks/SQL."""
    mock_fn = MagicMock(side_effect=Exception("SELECT * FROM secret_table syntax error"))
    with patch.dict(mcp_server.TOOLS, {"search_sources": mock_fn}):
        server = mcp_server.build_mcp()
        tool_obj = server._tool_manager._tools.get("search_sources")
        assert tool_obj is not None
        tool_fn = tool_obj.fn

        user = CurrentUser(
            id=uuid.uuid4(),
            email="mcp@test.local",
            is_admin=False,
            scopes=frozenset({"read", "write"}),
            via="pat",
        )
        token = mcp_server.current_user.set(user)
        try:
            result = tool_fn(query="test")
            assert result.get("error") == "internal_error"
            assert "SELECT" not in str(result)
            assert result.get("message") == "tool execution failed"
        finally:
            mcp_server.current_user.reset(token)
