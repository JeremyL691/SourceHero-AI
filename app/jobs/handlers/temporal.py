"""Temporal handlers: document reconciliation, wayback backfill, weekly diffs."""

from __future__ import annotations

from app.jobs.registry import JobContext, job_handler


@job_handler("reconcile_document")
def reconcile_document_job(ctx: JobContext, payload: dict) -> dict:
    """Recompute one document's claim lifecycle.

    Idempotent by construction -- reconcile_document recomputes from the full version
    set -- so a retry after a crash is safe.
    """
    import uuid

    from app.temporal.reconcile import reconcile_document

    document_id = payload.get("document_id")
    if not document_id:
        return {"skipped": "no document_id in payload"}
    stats = reconcile_document(ctx.db, uuid.UUID(str(document_id)))
    return stats.as_dict()


@job_handler("wayback_backfill")
def wayback_backfill_job(ctx: JobContext, payload: dict) -> dict:
    """Handled in app.ingestion.wayback; re-exported so the registry sees it."""
    from app.ingestion.wayback import wayback_backfill

    return wayback_backfill(ctx, payload)


@job_handler("belief_diff")
def belief_diff_job(ctx: JobContext, payload: dict) -> dict:
    """Compute and store one belief diff."""
    import uuid

    from app.temporal.belief_diff import compute_and_store_diff

    start = payload.get("start")
    end = payload.get("end")
    if not start or not end:
        return {"skipped": "start and end are required"}
    scope_kind = payload.get("scope_kind", "all")
    scope_id = payload.get("scope_id")
    row = compute_and_store_diff(
        ctx.db,
        ctx.user_id,
        start=_parse(start),
        end=_parse(end),
        scope_kind=scope_kind,
        scope_id=uuid.UUID(str(scope_id)) if scope_id else None,
        time_axis=payload.get("time_axis", "world"),
    )
    # `compute_and_store_diff` returns the persisted row; the counts live in its payload.
    return {"diff_id": str(row.id), "stats": (row.payload or {}).get("stats", {})}


def _parse(value):
    from datetime import UTC, datetime

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = ["belief_diff_job", "reconcile_document_job", "wayback_backfill_job"]
