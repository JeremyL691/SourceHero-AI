"""Liveness and readiness.

`/healthz` must never touch the database -- it answers "is this process alive".
`/readyz` is the one that actually checks DB, embedder and LLM wiring.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

router = APIRouter()


@router.get("/healthz", summary="Liveness probe", tags=["health"])
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe", tags=["health"])
def readyz() -> dict:
    from app.core.config import get_settings
    from app.core.db import get_engine

    settings = get_settings()
    checks: dict[str, object] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - the point is to report, not raise
        checks["database"] = f"error: {type(exc).__name__}: {exc}"

    try:
        from app.indexing.embeddings import get_embedder

        embedder = get_embedder()
        checks["embedder"] = getattr(embedder, "status", embedder.name)
    except Exception as exc:  # noqa: BLE001
        checks["embedder"] = f"error: {type(exc).__name__}: {exc}"

    checks["llm"] = settings.llm_provider

    ok = not any(str(v).startswith("error") for v in checks.values())
    return {"status": "ok" if ok else "degraded", "env": settings.sourcehero_env, "checks": checks}
