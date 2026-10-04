"""Application factory.

`create_app()` exists instead of a module-level singleton because tests build the app
many times per process, and the MCP mount must not be shared across those builds.
Routers are wired in as their milestone lands -- there are no 501 stubs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import (
    BudgetExceededError,
    ConflictError,
    DomainError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import get_logger

log = get_logger(__name__)

API_PREFIX = "/v1"


def _error_response(status: int, code: str, message: str, details: dict | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error_response(404, exc.code, exc.message, exc.details)

    @app.exception_handler(DomainValidationError)
    async def _invalid(_: Request, exc: DomainValidationError) -> JSONResponse:
        return _error_response(422, exc.code, exc.message, exc.details)

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return _error_response(409, exc.code, exc.message, exc.details)

    @app.exception_handler(PermissionDeniedError)
    async def _denied(_: Request, exc: PermissionDeniedError) -> JSONResponse:
        return _error_response(403, exc.code, exc.message, exc.details)

    @app.exception_handler(BudgetExceededError)
    async def _budget(_: Request, exc: BudgetExceededError) -> JSONResponse:
        return _error_response(429, exc.code, exc.message, exc.details)

    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Keep pydantic's per-field detail; the UI renders it next to the input.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "invalid request",
                    "details": {"fields": exc.errors()},
                }
            },
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("startup env=%s port=%s", settings.sourcehero_env, settings.api_port)
    try:
        from app.core.db import get_engine

        get_engine()
    except Exception:  # noqa: BLE001 - /readyz reports the real problem
        log.warning("startup: engine could not be created", exc_info=True)
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    # The API enqueues jobs it never runs, so it must know which kinds exist.
    # Registering here (rather than only in the worker) means a request that queues
    # `ingest_source` cannot fail later with "unknown job kind".
    from app.jobs.handlers import register_all

    register_all()

    settings = get_settings()
    app = FastAPI(
        title="SourceHero",
        version="2.0.0-rc1",
        summary="Claim-level, time-aware, verifiable research knowledge system.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_exception_handlers(app)

    from app.api.routers.auth import router as auth_router
    from app.api.routers.health import router as health_router
    from app.api.routers.jobs import router as jobs_router
    from app.api.routers.search import router as search_router
    from app.api.routers.settings import router as settings_router
    from app.api.routers.sources import router as sources_router

    app.include_router(health_router, tags=["health"])
    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(jobs_router, prefix=API_PREFIX)
    app.include_router(sources_router, prefix=API_PREFIX)
    app.include_router(search_router, prefix=API_PREFIX)
    app.include_router(settings_router, prefix=API_PREFIX)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return app


app = create_app()

__all__ = ["API_PREFIX", "app", "create_app"]
