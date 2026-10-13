"""Liveness and readiness.

`/healthz` must never touch the database -- it answers "is this process alive".
`/readyz` is the one that actually checks DB, embedder and LLM wiring.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter()


@router.get("/healthz", summary="Liveness probe", tags=["health"])
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe", tags=["health"])
def readyz():
    from app.core.config import get_settings
    from app.core.db import get_engine

    settings = get_settings()
    checks: dict[str, object] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - sanitized for external probes
        log.warning("readyz: database check failed: %s", exc)
        checks["database"] = "error"

    try:
        from app.indexing.embeddings import get_embedder

        embedder = get_embedder()
        checks["embedder"] = getattr(embedder, "status", embedder.name)
    except Exception as exc:  # noqa: BLE001 - sanitized
        log.warning("readyz: embedder check failed: %s", exc)
        checks["embedder"] = "error"

    checks["llm"] = settings.llm_provider

    ok = not any(str(v).startswith("error") for v in checks.values())
    content = {"status": "ok" if ok else "degraded", "env": settings.sourcehero_env, "checks": checks}
    if not ok:
        return JSONResponse(status_code=503, content=content)
    return content
