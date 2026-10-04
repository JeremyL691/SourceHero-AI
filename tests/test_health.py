"""App factory, health probes and the domain-error -> HTTP mapping."""

from __future__ import annotations

import pytest

from app.core.errors import (
    BudgetExceededError,
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.main import create_app


def test_healthz_is_the_exact_documented_body(client):
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.db
def test_readyz_reports_database_embedder_and_llm(client):
    body = client.get("/readyz").json()
    assert body["status"] in ("ok", "degraded")
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["embedder"].startswith("hashing-v1")
    # Development runs must default to the fake provider -- no paid API by accident.
    assert body["checks"]["llm"] == "fake"


def test_openapi_is_generated_and_every_route_has_a_summary(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/healthz" in paths
    for path, ops in paths.items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            assert op.get("summary"), f"{method.upper()} {path} has no summary"
            assert op.get("tags"), f"{method.upper()} {path} has no tags"


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (NotFoundError("gone"), 404),
        (DomainValidationError("bad input"), 422),
        (ConflictError("already exists"), 409),
        (PermissionDeniedError("nope"), 403),
        (BudgetExceededError("too many"), 429),
    ],
)
def test_domain_errors_map_to_status_codes(exc, status):
    """The mapping lives in create_app; assert it without needing a route."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.main import _register_exception_handlers

    app = FastAPI()
    _register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    assert TestClient(app, raise_server_exceptions=False).get("/boom").status_code == status


def test_error_bodies_have_a_stable_shape(make_client):
    """A domain error on a real route still carries `error.code` / `error.message`."""
    client = make_client(email="errors@test.local")
    resp = client.request("DELETE", "/v1/tokens/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert set(resp.json()["error"]) >= {"code", "message"}


def test_create_app_returns_independent_instances():
    """MCP mounts cannot be shared, so the factory must not be a singleton."""
    assert create_app() is not create_app()
