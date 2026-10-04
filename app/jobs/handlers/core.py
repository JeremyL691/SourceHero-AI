"""Handlers that are not tied to one domain."""

from __future__ import annotations

from app.jobs.registry import JobContext, job_handler


@job_handler("noop")
def noop(ctx: JobContext, payload: dict) -> dict:
    """Does nothing and says so.

    Exists so the queue can be exercised end to end without a domain: enqueue a
    `noop` job, run `python -m app.jobs.worker --once`, and watch it succeed.
    """
    return {"ran": True, "echo": payload}


@job_handler("maintenance")
def maintenance(ctx: JobContext, payload: dict) -> dict:
    """Housekeeping: reap stuck jobs, drop expired oauth states and cache rows."""
    from sqlalchemy import text

    from app.jobs import queue as q

    reaped = q.reap_stale(ctx.db, ctx.settings.job_stale_seconds)
    pruned: dict[str, int] = {}
    for key, sql in (
        ("oauth_states", "DELETE FROM oauth_states WHERE created_at < now() - interval '10 minutes'"),
        ("llm_cache", "DELETE FROM llm_cache WHERE created_at < now() - interval '30 days'"),
        ("llm_calls", "DELETE FROM llm_calls WHERE created_at < now() - interval '90 days'"),
    ):
        # Savepoint per statement: a missing table must not roll back the reaping
        # (or anything else) the same transaction already did.
        try:
            with ctx.db.begin_nested():
                pruned[key] = ctx.db.execute(text(sql)).rowcount or 0
        except Exception:  # noqa: BLE001 - the table arrives with its own milestone
            pruned[key] = 0
    return {"reaped": reaped, "pruned": pruned}
