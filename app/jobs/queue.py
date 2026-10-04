"""The Postgres job queue.

Every statement takes `:now` as a parameter instead of calling `now()`. Inside the
test harness's savepoint session Postgres pins `now()` to the *outer* transaction's
start, so a backoff computed from `now()` would be silently wrong in tests. Passing
the clock in keeps production and tests on the same code path.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.job import Job

log = get_logger(__name__)

TERMINAL = ("succeeded", "failed", "dead", "cancelled")

# `ON CONFLICT (dedupe_key) WHERE status = 'queued'` must match the partial unique
# index created in 0001_baseline exactly, or Postgres will not use it.
ENQUEUE_SQL = text(
    """
    INSERT INTO jobs (user_id, kind, payload, status, priority, run_after, attempts,
                      max_attempts, dedupe_key, parent_job_id)
    VALUES (:user_id, :kind, CAST(:payload AS jsonb), 'queued', :priority, :run_after,
            0, :max_attempts, :dedupe_key, :parent_job_id)
    ON CONFLICT (dedupe_key) WHERE status = 'queued' DO NOTHING
    RETURNING *
    """
)

DEQUEUE_SQL = text(
    """
    UPDATE jobs SET status='running', locked_by=:worker, locked_at=:now,
                    heartbeat_at=:now, attempts=attempts+1
    WHERE id = (
      SELECT id FROM jobs
      WHERE status='queued' AND (run_after <= :now OR :drain)
      ORDER BY priority DESC, run_after, id
      FOR UPDATE SKIP LOCKED
      LIMIT 1)
    RETURNING *
    """
)


def enqueue(
    db: Session,
    kind: str,
    payload: dict,
    *,
    user_id: uuid.UUID | None = None,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    priority: int = 0,
    max_attempts: int = 5,
    parent_job_id: int | None = None,
    now: datetime | None = None,
) -> Job:
    """Add a job, or return the already-pending one that shares its `dedupe_key`.

    A job that is already *running* does not block a new one: changes that land
    while a handler is mid-flight must not be lost, so only `queued` collides.
    """
    now = now or clock.utcnow()
    run_after = run_after or now
    row = (
        db.execute(
            ENQUEUE_SQL,
            {
                "user_id": user_id,
                "kind": kind,
                "payload": json.dumps(payload or {}),
                "priority": priority,
                "run_after": run_after,
                "max_attempts": max_attempts,
                "dedupe_key": dedupe_key,
                "parent_job_id": parent_job_id,
            },
        )
        .mappings()
        .first()
    )

    if row is None and dedupe_key:
        existing = (
            db.execute(
                text("SELECT * FROM jobs WHERE dedupe_key = :dedupe_key AND status = 'queued'"),
                {"dedupe_key": dedupe_key},
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return _attach(db, existing)

    if row is None:  # a concurrent insert beat us to the dedupe key
        existing = (
            db.execute(
                text("SELECT * FROM jobs WHERE dedupe_key = :dedupe_key ORDER BY id DESC LIMIT 1"),
                {"dedupe_key": dedupe_key},
            )
            .mappings()
            .first()
        )
        if existing is not None:
            return _attach(db, existing)

    job = _attach(db, row)
    if get_settings().jobs_inline:
        from app.jobs.registry import run_inline

        run_inline(db, job, now=now)
        db.refresh(job)
    return job


def _attach(db: Session, row) -> Job:
    """Rehydrate a raw `jobs` row into the ORM object."""
    job = db.get(Job, row["id"])
    if job is None:  # pragma: no cover - row was just written
        job = Job(id=row["id"])
        db.add(job)
    return job


def dequeue(db: Session, *, worker: str, drain: bool = False, now: datetime | None = None) -> Job | None:
    """Claim the highest-priority due job. `SKIP LOCKED` keeps workers from blocking."""
    now = now or clock.utcnow()
    row = db.execute(DEQUEUE_SQL, {"worker": worker, "now": now, "drain": drain}).mappings().first()
    if row is None:
        return None
    return _attach(db, row)


def complete(db: Session, job_id: int, result: dict | None = None, *, now: datetime | None = None) -> None:
    now = now or clock.utcnow()
    db.execute(
        text(
            """
            UPDATE jobs SET status='succeeded', finished_at=:now, result=CAST(:result AS jsonb),
                            locked_by=NULL, locked_at=NULL
            WHERE id=:id
            """
        ),
        {"id": job_id, "now": now, "result": _json(result)},
    )


def backoff_seconds(attempts: int) -> float:
    """Exponential with a cap, plus jitter so a fleet does not retry in lockstep."""
    delay = min(2**attempts * 10, 1800)
    return delay + random.uniform(0, 5)


def fail(
    db: Session,
    job_id: int,
    error: str,
    *,
    retryable: bool = True,
    now: datetime | None = None,
) -> None:
    now = now or clock.utcnow()
    row = (
        db.execute(text("SELECT attempts, max_attempts FROM jobs WHERE id=:id"), {"id": job_id})
        .mappings()
        .first()
    )
    if row is None:  # pragma: no cover
        return
    attempts, max_attempts = int(row["attempts"]), int(row["max_attempts"])

    if retryable and attempts < max_attempts:
        status, run_after, finished_at = "queued", now + timedelta(seconds=backoff_seconds(attempts)), None
    else:
        status, run_after, finished_at = ("dead" if retryable else "failed"), None, now

    db.execute(
        text(
            """
            UPDATE jobs SET status=:status, run_after=COALESCE(:run_after, run_after),
                            finished_at=:finished_at, last_error=:error,
                            locked_by=NULL, locked_at=NULL
            WHERE id=:id
            """
        ),
        {
            "id": job_id,
            "status": status,
            "run_after": run_after,
            "finished_at": finished_at,
            "error": (error or "")[:2000],
        },
    )


def heartbeat(db: Session, job_id: int, *, now: datetime | None = None) -> None:
    db.execute(
        text("UPDATE jobs SET heartbeat_at=:now WHERE id=:id"),
        {"id": job_id, "now": now or clock.utcnow()},
    )


def reap_stale(db: Session, stale_seconds: int, *, now: datetime | None = None) -> int:
    """Return jobs whose worker died to the queue so another worker can pick them up."""
    now = now or clock.utcnow()
    cutoff = now - timedelta(seconds=stale_seconds)
    result = db.execute(
        text(
            """
            UPDATE jobs SET status='queued', locked_by=NULL, locked_at=NULL, heartbeat_at=NULL
            WHERE status='running'
              AND (heartbeat_at IS NULL OR heartbeat_at < :cutoff)
            RETURNING id
            """
        ),
        {"cutoff": cutoff},
    )
    return len(result.fetchall())


def cancel(db: Session, job_id: int) -> bool:
    """Cancel a queued job outright; a running one is only asked to stop."""
    now = clock.utcnow()
    claimed = db.execute(
        text("UPDATE jobs SET status='cancelled', finished_at=:now WHERE id=:id AND status='queued'"),
        {"id": job_id, "now": now},
    ).rowcount
    if claimed:
        return True
    db.execute(
        text(
            """
            UPDATE jobs SET payload = jsonb_set(payload, '{_cancel}', 'true'::jsonb, true)
            WHERE id=:id AND status='running'
            """
        ),
        {"id": job_id},
    )
    return False


def retry(db: Session, job_id: int, *, now: datetime | None = None) -> bool:
    now = now or clock.utcnow()
    result = db.execute(
        text(
            """
            UPDATE jobs SET status='queued', attempts=0, run_after=:now, last_error=NULL,
                            locked_by=NULL, locked_at=NULL, finished_at=NULL
            WHERE id=:id AND status IN ('failed', 'dead')
            """
        ),
        {"id": job_id, "now": now},
    )
    return bool(result.rowcount)


def pending(
    db: Session, *, user_id: uuid.UUID | None = None, drain: bool = False, now: datetime | None = None
) -> int:
    """How many jobs are due (or, in drain mode, how many are still queued)."""
    from sqlalchemy import func, select

    now = now or clock.utcnow()
    if drain:
        stmt = select(func.count()).select_from(Job).where(Job.status == "queued")
    else:
        stmt = select(func.count()).select_from(Job).where(Job.status == "queued", Job.run_after <= now)
    if user_id is not None:
        stmt = stmt.where(Job.user_id == user_id)
    return db.execute(stmt).scalar_one()


def _json(value: dict | None) -> str:
    return json.dumps(value or {})


__all__ = [
    "DEQUEUE_SQL",
    "ENQUEUE_SQL",
    "TERMINAL",
    "backoff_seconds",
    "cancel",
    "complete",
    "dequeue",
    "enqueue",
    "fail",
    "heartbeat",
    "pending",
    "reap_stale",
    "retry",
]
