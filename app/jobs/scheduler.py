"""The scheduler tick.

Runs in the worker's main thread. Multiple workers may be running, so the tick is
guarded by a transaction-scoped advisory lock: exactly one of them schedules per tick.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs import queue as q
from app.jobs.schedules import compute_next_run_at
from app.models.job import ScheduledJob
from app.models.source import Source

log = get_logger(__name__)

_stop = threading.Event()

MAINTENANCE_INTERVAL_SECONDS = 300
_last_maintenance_at: datetime | None = None


def _try_tick_lock(db: Session) -> bool:
    """True when this worker is the one that should run the tick."""
    return bool(
        db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext('sourcehero-scheduler'))")).scalar()
    )


def _tick_scheduled_jobs(db: Session, now: datetime) -> int:
    due = (
        db.execute(
            select(ScheduledJob).where(
                ScheduledJob.enabled.is_(True),
                ScheduledJob.next_run_at.is_not(None),
                ScheduledJob.next_run_at <= now,
            )
        )
        .scalars()
        .all()
    )

    for job in due:
        # The dedupe key carries next_run_at, so a job that fired twice for the same
        # scheduled slot cannot be enqueued twice even if two ticks overlap.
        q.enqueue(
            db,
            job.job_type,
            dict(job.config or {}),
            user_id=job.user_id,
            dedupe_key=f"sched:{job.id}:{job.next_run_at.isoformat()}",
            now=now,
        )
        job.last_run_at = now
        job.next_run_at = compute_next_run_at(job, after=now)
    return len(due)


def _tick_watch_sources(db: Session, now: datetime) -> int:
    due = (
        db.execute(
            select(Source).where(
                Source.watch.is_(True),
                Source.status == "active",
                Source.next_refresh_at.is_not(None),
                Source.next_refresh_at <= now,
            )
        )
        .scalars()
        .all()
    )

    for source in due:
        q.enqueue(
            db,
            "refresh_source",
            {"source_id": str(source.id)},
            user_id=source.user_id,
            dedupe_key=f"watch:{source.id}:{source.next_refresh_at.isoformat()}",
            now=now,
        )
        source.next_refresh_at = now + timedelta(minutes=source.refresh_interval_minutes or 1440)
    return len(due)


def _tick_weekly_diff(db: Session, now: datetime) -> int:
    """Weekly belief diffs: enqueue a diff job for every user with active watches."""
    from app.models.job import Job
    from app.models.temporal import Watch

    user_ids = list(db.execute(select(Watch.user_id).distinct()).scalars())
    if not user_ids:
        return 0

    iso_year, iso_week, _ = now.isocalendar()
    week_str = f"{iso_year}-W{iso_week:02d}"
    enqueued = 0

    for uid in user_ids:
        dedupe_key = f"diff:{uid}:{week_str}:all"
        exists = db.execute(select(Job.id).where(Job.dedupe_key == dedupe_key)).scalar()
        if exists is not None:
            continue
        q.enqueue(
            db,
            "belief_diff",
            {
                "start": (now - timedelta(days=7)).isoformat(),
                "end": now.isoformat(),
                "scope_kind": "all",
                "scope_id": None,
                "time_axis": "world",
            },
            user_id=uid,
            dedupe_key=dedupe_key,
            now=now,
        )
        enqueued += 1
    return enqueued


def _tick_query_watches(db: Session, now: datetime) -> int:
    """Evaluate query watches against newly asserted claims since last_checked_at."""
    from app.claims.cluster import thresholds
    from app.indexing.embeddings import cosine
    from app.models.claim import Claim
    from app.models.temporal import Watch
    from app.temporal.belief_diff import compute_and_store_diff
    from app.temporal.watches import mark_checked

    watches = list(
        db.execute(select(Watch).where(Watch.kind == "query", Watch.embedding.is_not(None))).scalars()
    )
    if not watches:
        return 0

    hit_count = 0
    pair_sim = thresholds()["pair_sim"]

    for watch in watches:
        since = watch.last_checked_at or watch.created_at
        new_claims = list(
            db.execute(
                select(Claim).where(
                    Claim.user_id == watch.user_id,
                    Claim.asserted_at >= since,
                    Claim.embedding.is_not(None),
                )
            ).scalars()
        )
        matching = [
            c for c in new_claims if cosine(list(watch.embedding), list(c.embedding or [])) >= pair_sim
        ]
        if matching:
            compute_and_store_diff(
                db,
                watch.user_id,
                start=since,
                end=now,
                scope_kind="watch",
                scope_id=watch.id,
                time_axis="world",
            )
            hit_count += 1
        mark_checked(db, watch, at=now)
    return hit_count


def _tick_maintenance(db: Session, now: datetime, *, force: bool = False) -> None:
    """Reap stuck jobs and clear expired oauth states / llm cache rows."""
    global _last_maintenance_at
    if (
        not force
        and _last_maintenance_at is not None
        and (now - _last_maintenance_at).total_seconds() < MAINTENANCE_INTERVAL_SECONDS
    ):
        return
    _last_maintenance_at = now
    settings = get_settings()
    reaped = q.reap_stale(db, settings.job_stale_seconds, now=now)
    if reaped:
        log.info("maintenance: reaped %d stale job(s)", reaped)
    for sql in (
        "DELETE FROM oauth_states WHERE created_at < now() - interval '10 minutes'",
        "DELETE FROM llm_cache WHERE created_at < now() - interval '30 days'",
        "DELETE FROM llm_calls WHERE created_at < now() - interval '90 days'",
    ):
        # Each prune runs in its own savepoint: before its milestone lands the table does
        # not exist, and a plain failure would roll back the whole tick -- including the
        # jobs it just enqueued.
        try:
            with db.begin_nested():
                db.execute(text(sql))
        except Exception:  # noqa: BLE001 - the table may not exist yet (pre-M6)
            log.debug("maintenance: skipping %s", sql.split()[2])


def tick(db: Session, *, now: datetime | None = None) -> dict:
    """One scheduling pass. Idempotent: running it twice must not double-enqueue."""
    now = now or clock.utcnow()
    if not _try_tick_lock(db):
        return {"skipped": "another worker is scheduling"}

    counts = {
        "scheduled_jobs": _tick_scheduled_jobs(db, now),
        "watch_sources": _tick_watch_sources(db, now),
        "weekly_diff": _tick_weekly_diff(db, now),
        "query_watches": _tick_query_watches(db, now),
    }
    _tick_maintenance(db, now)
    db.commit()
    return counts


def scheduler_loop() -> None:
    """Thread body: sleep one tick interval, then schedule."""
    settings = get_settings()
    interval = max(1.0, settings.scheduler_tick_seconds)
    from app.core.db import SessionLocal

    while not _stop.wait(interval):
        db = SessionLocal()
        try:
            tick(db)
        except Exception:  # noqa: BLE001 - never let a tick kill the worker
            db.rollback()
            log.exception("scheduler tick failed")
        finally:
            db.close()


def stop_scheduler() -> None:
    _stop.set()


__all__ = ["MAINTENANCE_INTERVAL_SECONDS", "scheduler_loop", "stop_scheduler", "tick"]
