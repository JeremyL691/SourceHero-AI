"""Handler registry and `JobContext`.

Handlers are plain functions `(ctx, payload) -> dict | None`, registered by kind.
They must be idempotent: the queue guarantees at-least-once delivery, so a retry has
to produce the same rows, not duplicates.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.job import Job

log = get_logger(__name__)

Handler = Callable[["JobContext", dict], "dict | None"]

HANDLERS: dict[str, Handler] = {}


class JobCancelled(Exception):
    """Raised by `ctx.check_cancelled()` when a running job was asked to stop."""


class JobDeferred(Exception):
    """Raised when a job yields because of lock contention, without consuming attempts."""

    def __init__(self, delay_seconds: float = 5.0):
        super().__init__(f"job deferred for {delay_seconds}s")
        self.delay_seconds = delay_seconds


class UnknownJobKind(Exception):
    """No handler is registered for this kind; retrying cannot help."""


class NonRetryableJobError(Exception):
    """A handler failure that retrying will not fix.

    The queue records these as `failed` instead of `queued`, so a paused source or a
    404 does not burn five attempts and a backoff window on every refresh tick.
    """


def job_handler(kind: str) -> Callable[[Handler], Handler]:
    def wrap(fn: Handler) -> Handler:
        if kind in HANDLERS and HANDLERS[kind] is not fn:
            raise ValueError(f"job handler for kind '{kind}' already registered: {HANDLERS[kind]}")
        HANDLERS[kind] = fn
        return fn

    return wrap


@dataclass
class JobContext:
    db: Session
    job: Job
    user_id: uuid.UUID | None
    logger: Any = field(default=log)
    settings: Settings = field(default_factory=get_settings)

    def enqueue(
        self,
        kind: str,
        payload: dict,
        *,
        user_id: uuid.UUID | None = None,
        dedupe_key: str | None = None,
        run_after: datetime | None = None,
        priority: int = 0,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> Job:
        """Queue follow-up work, already linked to this job for tracing."""
        from app.jobs.queue import enqueue as _enqueue

        return _enqueue(
            self.db,
            kind,
            payload,
            user_id=user_id if user_id is not None else self.user_id,
            dedupe_key=dedupe_key,
            run_after=run_after,
            priority=priority,
            max_attempts=max_attempts,
            parent_job_id=self.job.id,
            now=now,
        )

    def check_cancelled(self) -> None:
        from sqlalchemy import text

        from app.core.db import SessionLocal

        with SessionLocal() as s:
            payload = s.execute(
                text("SELECT payload FROM jobs WHERE id = :id"),
                {"id": self.job.id},
            ).scalar()
            if (payload or {}).get("_cancel"):
                raise JobCancelled("cancellation requested")

    def progress(self, data: dict) -> None:
        """Merge progress into `job.result` via an independent session so the UI can see it immediately."""
        import json

        from sqlalchemy import text

        from app.core.db import SessionLocal

        try:
            with SessionLocal() as s:
                s.execute(text("SET LOCAL lock_timeout = '500ms'"))
                current = (
                    s.execute(
                        text("SELECT result FROM jobs WHERE id = :id"),
                        {"id": self.job.id},
                    ).scalar()
                    or {}
                )
                prog = {**(current.get("progress") or {}), **data}
                current["progress"] = prog
                s.execute(
                    text("UPDATE jobs SET result = CAST(:res AS jsonb) WHERE id = :id"),
                    {"res": json.dumps(current), "id": self.job.id},
                )
                s.commit()
        except Exception:
            res = dict(self.job.result or {})
            res["progress"] = {**(res.get("progress") or {}), **data}
            self.job.result = res
            self.db.flush()
        else:
            res = dict(self.job.result or {})
            res["progress"] = {**(res.get("progress") or {}), **data}
            self.job.result = res


def get_handler(kind: str) -> Handler:
    handler = HANDLERS.get(kind)
    if handler is None:
        raise UnknownJobKind(kind)
    return handler


def run_inline(db: Session, job: Job, *, now: datetime | None = None) -> Job:
    """Execute a handler synchronously, with the worker's exact status transitions.

    On failure the job is marked `failed` (never retried -- a test must not spin) and
    the original exception is re-raised so the caller sees the real cause, unless
    `JOBS_INLINE_SWALLOW` is set.
    """
    import os

    from app.jobs.queue import complete, fail

    now = now or clock.utcnow()
    job_id, kind, payload = job.id, job.kind, dict(job.payload or {})
    ctx = JobContext(db=db, job=job, user_id=job.user_id, logger=log)
    try:
        handler = get_handler(kind)
    except UnknownJobKind as exc:
        # An unknown kind can never succeed, so do not burn retries on it.
        fail(db, job_id, f"unknown job kind: {kind}", retryable=False, now=now)
        db.flush()
        raise exc from exc
    from sqlalchemy import text

    db.execute(
        text("UPDATE jobs SET status='running', locked_by='inline', attempts=attempts+1 WHERE id=:id"),
        {"id": job_id},
    )
    db.flush()

    try:
        result = handler(ctx, payload)
        complete(db, job_id, result or {}, now=now)
        db.flush()
        db.refresh(job)
        return job
    except JobCancelled:
        from sqlalchemy import text

        db.execute(
            text(
                "UPDATE jobs SET status='cancelled', finished_at=:now, locked_by=NULL, "
                "locked_at=NULL WHERE id=:id"
            ),
            {"now": now, "id": job_id},
        )
        db.flush()
        db.refresh(job)
        return job
    except Exception as exc:  # noqa: BLE001 - the handler owns its own errors
        db.rollback()
        fail(db, job_id, str(exc) or type(exc).__name__, retryable=False, now=now)
        db.flush()
        if os.environ.get("JOBS_INLINE_SWALLOW") == "1":
            log.warning("inline job %s failed: %s", kind, exc)
            db.refresh(job)
            return job
        raise


__all__ = [
    "HANDLERS",
    "Handler",
    "JobCancelled",
    "JobContext",
    "JobDeferred",
    "NonRetryableJobError",
    "UnknownJobKind",
    "get_handler",
    "job_handler",
    "run_inline",
]
