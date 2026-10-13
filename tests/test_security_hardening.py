"""Regression tests for security hardening (SEC-7, SEC-9, SEC-10, SEC-11)."""

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


class _RecordingStorage:
    def __init__(self) -> None:
        self.uploads: list[str] = []

    def upload(self, key: str, fileobj, content_type: str) -> None:
        self.uploads.append(key)


@pytest.fixture
def recording_storage(monkeypatch):
    import app.storage

    storage = _RecordingStorage()
    monkeypatch.setattr(app.storage, "get_storage", lambda: storage)
    return storage


def test_store_upload_refuses_fake_pdf_and_stores_nothing(recording_storage):
    """A .pdf whose bytes lack the %PDF- header is a 415 and never reaches storage."""
    from app.core.errors import UnsupportedMediaTypeError
    from app.ingestion.sources import store_upload

    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        store_upload(uuid.uuid4(), "report.pdf", b"<html>definitely not a pdf</html>")

    assert exc_info.value.status_code == 415
    assert exc_info.value.code == "bad_file_type"
    assert recording_storage.uploads == []


def test_store_upload_accepts_pdf_with_real_header(recording_storage):
    from app.ingestion.sources import store_upload

    key, source_type = store_upload(uuid.uuid4(), "report.PDF", b"%PDF-1.7\n1 0 obj\n%%EOF\n")

    assert source_type == "pdf"
    assert key.endswith(".pdf")
    assert recording_storage.uploads == [key]


def test_upload_endpoint_refuses_fake_pdf_and_lists_no_source(make_client, recording_storage):
    client = make_client(email="fakepdf@test.local")

    resp = client.post(
        "/v1/sources/upload",
        files={"file": ("notes.pdf", io.BytesIO(b"plain text pretending to be a pdf"), "application/pdf")},
    )

    assert resp.status_code == 415, resp.text
    assert resp.json()["error"]["code"] == "bad_file_type"
    assert recording_storage.uploads == []
    listing = client.get("/v1/sources").json()
    items = listing["items"] if isinstance(listing, dict) else listing
    assert items == []


def test_upload_endpoint_accepts_pdf_with_header(make_client, recording_storage, monkeypatch):
    import app.jobs.queue

    monkeypatch.setattr(app.jobs.queue, "enqueue", lambda *args, **kwargs: None)
    client = make_client(email="realpdf@test.local")

    resp = client.post(
        "/v1/sources/upload",
        files={"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "application/pdf")},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["source_type"] == "pdf"
    assert len(recording_storage.uploads) == 1
