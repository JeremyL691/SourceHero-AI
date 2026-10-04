"""LLM settings, credentials, OAuth and usage. Administrator-only."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_admin, require_admin_write
from app.core import time as clock
from app.core.config import get_settings
from app.core.errors import DomainValidationError
from app.llm.client import get_llm, invalidate_llm
from app.llm.credentials import oauth_pkce
from app.llm.credentials.store import (
    credential_status,
    delete_credential,
    save_credential,
)
from app.llm.router import (
    clear_db_overrides,
    effective_llm_settings,
    redact,
    write_db_overrides,
)
from app.models.llm import LLMRun

router = APIRouter(tags=["settings"])


@router.get("/settings/llm", summary="LLM settings (secrets redacted)")
def get_llm_settings(user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    return redact(effective_llm_settings(db, settings), settings, db)


@router.put("/settings/llm", summary="Update LLM settings")
def put_llm_settings(
    payload: dict,
    user: CurrentUser = Depends(require_admin_write),
    db: Session = Depends(get_db),
) -> dict:
    allowed = {
        "provider",
        "base_url",
        "model_fast",
        "model_smart",
        "auth",
        "auth_header",
        "json_mode",
        "extra_headers",
        "daily_token_budget",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise DomainValidationError(
            f"unknown setting(s): {', '.join(sorted(unknown))}", code="unknown_setting"
        )
    write_db_overrides(db, payload)
    invalidate_llm()
    settings = get_settings()
    return redact(effective_llm_settings(db, settings), settings, db)


@router.delete("/settings/llm", summary="Clear LLM settings overrides")
def delete_llm_settings(
    user: CurrentUser = Depends(require_admin_write), db: Session = Depends(get_db)
) -> dict:
    clear_db_overrides(db)
    invalidate_llm()
    settings = get_settings()
    return redact(effective_llm_settings(db, settings), settings, db)


@router.get("/settings/llm/models", summary="List models the endpoint offers")
def list_models(user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    client = get_llm(db)
    try:
        return {"models": client.list_models()}
    except Exception as exc:  # noqa: BLE001 - surfaced to the settings page verbatim
        raise DomainValidationError(str(exc), code="list_models_failed") from exc


@router.post("/settings/llm/credentials", summary="Store an API key or token command")
def post_credentials(
    payload: dict,
    user: CurrentUser = Depends(require_admin_write),
    db: Session = Depends(get_db),
) -> dict:
    kind = payload.get("kind")
    value = payload.get("value")
    if kind not in ("api_key", "command"):
        raise DomainValidationError("kind must be 'api_key' or 'command'", code="bad_credential_kind")
    if not value:
        raise DomainValidationError("value is required", code="missing_value")

    provider = payload.get("provider") or effective_llm_settings(db).preset or "custom"
    save_credential(db, provider, kind, {"value": value})
    invalidate_llm()
    settings = get_settings()
    # Report the provider this call actually wrote to, not the active one: they are
    # the same by default but need not be, and a status read from the wrong provider
    # would tell the operator their key was not stored.
    return {
        "provider": provider,
        **redact(effective_llm_settings(db, settings), settings, db),
        **credential_status(db, provider),
    }


@router.delete("/settings/llm/credentials", summary="Delete stored credentials")
def remove_credentials(
    provider: str | None = None,
    user: CurrentUser = Depends(require_admin_write),
    db: Session = Depends(get_db),
) -> dict:
    target = provider or effective_llm_settings(db).preset or "custom"
    removed = delete_credential(db, target)
    invalidate_llm()
    settings = get_settings()
    return {
        "removed": removed,
        "provider": target,
        **redact(effective_llm_settings(db, settings), settings, db),
        **credential_status(db, target),
    }


@router.get("/settings/llm/oauth/start", summary="Begin the OAuth PKCE flow")
def oauth_start(user: CurrentUser = Depends(require_admin_write), db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    provider = effective_llm_settings(db).preset or "custom"
    return {"authorize_url": oauth_pkce.start_authorization(db, settings, provider)}


@router.get("/settings/llm/oauth/callback", summary="Complete the OAuth PKCE flow")
def oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Browser redirect target, so it carries no JWT -- the state row is the guard."""
    settings = get_settings()
    base = settings.public_web_url.rstrip("/")
    try:
        oauth_pkce.complete_authorization(db, settings, code=code, state=state)
        invalidate_llm()
        target = f"{base}/app/settings?oauth=ok"
    except Exception as exc:  # noqa: BLE001 - the browser gets a reason, not a stack trace
        reason = getattr(exc, "code", "oauth_failed")
        target = f"{base}/app/settings?oauth=error&reason={reason}"
    return RedirectResponse(target, status_code=302)


@router.post("/settings/llm/test", summary="Send a test request on both tiers")
def test_connection(user: CurrentUser = Depends(require_admin_write), db: Session = Depends(get_db)) -> dict:
    from app.llm.types import LLMRequest, Message

    client = get_llm(db)
    out: dict[str, dict] = {}
    for tier in ("fast", "smart"):
        model = client.model_for(LLMRequest(task="connection_test", messages=[], tier=tier))
        try:
            result = client.complete(
                LLMRequest(
                    task="connection_test",
                    messages=[Message("user", "Reply with OK")],
                    tier=tier,
                    cache=False,
                )
            )
            out[tier] = {
                "ok": True,
                "model": result.model,
                "latency_ms": result.latency_ms,
                "output": result.text[:200],
            }
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            out[tier] = {
                "ok": False,
                "model": model,
                "error": str(exc),
                "code": getattr(exc, "code", "error"),
            }
    return {"provider": client.effective.provider, "mode": client.mode, "tiers": out}


@router.get("/usage", summary="Token usage by day, task and model")
def usage(
    days: int = Query(7, ge=1, le=365),
    user: CurrentUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    since = clock.utcnow() - timedelta(days=days)
    rows = db.execute(select(LLMRun).where(LLMRun.created_at >= since)).scalars().all()
    return summarise_usage(rows, days)


def summarise_usage(rows, days: int) -> dict:
    """Aggregate call rows into the shape the settings page and the MCP tools read."""
    by_day: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hits": 0, "errors": 0}

    for row in rows:
        day = row.created_at.date().isoformat()
        task = row.task
        for bucket, key in ((by_day, day), (by_task, task)):
            entry = bucket.setdefault(
                key,
                {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_hits": 0, "errors": 0},
            )
            entry["calls"] += 1
            entry["prompt_tokens"] += row.prompt_tokens
            entry["completion_tokens"] += row.completion_tokens
            entry["cache_hits"] += 1 if row.cache_hit else 0
            entry["errors"] += 1 if row.status != "ok" else 0

        totals["calls"] += 1
        totals["prompt_tokens"] += row.prompt_tokens
        totals["completion_tokens"] += row.completion_tokens
        totals["cache_hits"] += 1 if row.cache_hit else 0
        totals["errors"] += 1 if row.status != "ok" else 0

    calls = totals["calls"] or 1
    return {
        "days": days,
        "totals": {
            **totals,
            "cache_hit_rate": round(totals["cache_hits"] / calls, 4),
            "error_rate": round(totals["errors"] / calls, 4),
        },
        "by_day": dict(sorted(by_day.items())),
        "by_task": dict(sorted(by_task.items())),
    }


__all__ = ["router", "summarise_usage"]
