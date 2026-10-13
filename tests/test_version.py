"""One version string, read from package metadata, reported by every surface .

`pyproject.toml` is the source. The API document and the MCP server must both report it,
so a release cannot ship with three different numbers again (REVIEW DOC-10).
"""

from __future__ import annotations

from importlib import metadata

from fastapi.testclient import TestClient

from app.core.version import PACKAGE_NAME, UNKNOWN_VERSION, package_version


def test_package_version_is_the_installed_metadata():
    assert package_version() == metadata.version(PACKAGE_NAME)
    assert package_version() != UNKNOWN_VERSION


def test_openapi_document_reports_the_package_version():
    from app.main import app

    body = TestClient(app).get("/openapi.json").json()
    assert body["info"]["version"] == package_version()


def test_mcp_server_reports_the_package_version():
    from app.mcp.server import build_mcp

    assert build_mcp().version == package_version()
