"""The worker process: N threads pulling from the queue, plus a heartbeat and a
scheduler tick.

    python -m app.jobs.worker [--concurrency N] [--once] [--drain] [--kinds a,b]
    python -m app.jobs.worker --healthcheck

`--once`  process whatever is due, then exit.
`--drain` ignore `run_after` entirely and keep going until the queue is empty,
          including jobs enqueued while draining. Used by `make demo`, scripts and
          tests so that debounced follow-ups (plan_topics, compile_wiki_page) finish
          before the process exits.
`--healthcheck` exit 0 when the database answers and the worker heartbeat file was
          written within the last two minutes; exit 1 otherwise. For container probes.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, get_engine
from app.core.logging import (
    configure_logging,
    get_logger,
    hash_user_id,
    job_id_var,
    request_id_var,
    user_var,
)
from app.core.metrics import JOB_DURATION
from app.jobs import queue as q
from app.jobs.handlers import register_all
from app.jobs.registry import (
    JobCancelled,
    NonRetryableJobError,
    UnknownJobKind,
    get_handler,
)

log = get_logger(__name__)

HEARTBEAT_SECONDS = 15.0
HEARTBEAT_FILE = "worker.heartbeat"
# A heartbeat older than this means the worker loop has stopped making progress.
HEALTHY_HEARTBEAT_AGE_SECONDS = 120.0
# Runaway guard for `--drain`. Reaching it means a handler keeps enqueueing work.
MAX_DRAIN_JOBS = 10_000
SHUTDOWN_GRACE_SECONDS = 30.0

_stop = threading.Event()


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def heartbeat_path() -> Path:
    return Path(get_settings().data_dir) / HEARTBEAT_FILE


def write_heartbeat(path: Path | None = None) -> None:
    """Touch the heartbeat file atomically so a reader never sees a partial write."""
    path = path or heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(str(time.time()))
    os.replace(tmp, path)


def healthcheck(now: float | None = None) -> int:
    """0 when the database answers and the heartbeat is fresh, 1 otherwise."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure is "unhealthy"
        log.error("healthcheck: database unreachable: %s", exc)
        return 1
    try:
        mtime = heartbeat_path().stat().st_mtime
    except OSError:
        log.error("healthcheck: no heartbeat file at %s", heartbeat_path())
        return 1
    age = (time.time() if now is None else now) - mtime
    if age > HEALTHY_HEARTBEAT_AGE_SECONDS:
        log.error("healthcheck: heartbeat is %.0fs old", age)
        return 1
    return 0


def _enter_job_context(job) -> tuple:  # noqa: ANN001 - a Job row
    """Stamp job_id and the originating request id (if any) onto this thread's log records."""
    meta = (job.payload or {}).get("_meta") or {}
    request_id = meta.get("request_id") if isinstance(meta, dict) else None
    return (
        job_id_var.set(job.id),
        request_id_var.set(request_id),
        user_var.set(hash_user_id(str(job.user_id)) if job.user_id else None),
    )


def _exit_job_context(tokens: tuple) -> None:
    job_token, request_token, user_token = tokens
    user_var.reset(user_token)
    request_id_var.reset(request_token)
    job_id_var.reset(job_token)


def _run_one(db, worker_id: str, drain: bool, kinds: set[str] | list[str] | None) -> bool:
    """Claim and execute one job in 3 phases. Returns True when work was done.

    Phase 1: Claim transaction. Dequeue and commit immediately, so status='running',
             attempts+1, and locked_by are visible to other sessions and lock contention
             on dedupe_key is eliminated.
    Phase 2: Handler transaction. Run the handler in a new transaction. On success,
             complete() and commit.
    Phase 3: Failure handling. If handler raised an error, rollback, then in a new
             transaction handle JobCancelled, JobDeferred, or fail() and commit.
    """
    job = q.dequeue(db, worker=worker_id, drain=drain, kinds=kinds)
    if job is None:
        return False
    db.commit()

    kind = job.kind
    context_tokens = _enter_job_context(job)
    started = time.monotonic()
    outcome = "failed"
    try:
        outcome = _execute_claimed(db, job, worker_id, drain)
    finally:
        JOB_DURATION.labels(kind=kind, status=outcome).observe(time.monotonic() - started)
        _exit_job_context(context_tokens)
    return True


def _execute_claimed(db, job, worker_id: str, drain: bool) -> str:  # noqa: ANN001 - a Job row
    """Phases 2 and 3 for a job already claimed by `_run_one`. Returns the outcome label."""
    ctx_user = job.user_id
    from app.jobs.registry import (
        JobContext,
        JobDeferred,
    )

    started = time.monotonic()
    try:
        handler = get_handler(job.kind)
    except UnknownJobKind as exc:
        q.fail(db, job.id, str(exc), retryable=False, worker=worker_id)
        db.commit()
        log.warning("unknown job kind %s (id=%s)", job.kind, job.id)
        return "failed"

    try:
        result = handler(
            JobContext(db=db, job=job, user_id=ctx_user, logger=log),
            dict(job.payload or {}),
        )
        q.complete(db, job.id, result or {}, worker=worker_id)
        db.commit()
        log.info("job %s#%s ok in %.2fs", job.kind, job.id, time.monotonic() - started)
        return "succeeded"
    except JobCancelled:
        db.rollback()
        q.mark_cancelled(db, job.id, worker=worker_id)
        db.commit()
        log.info("job %s#%s cancelled", job.kind, job.id)
        return "cancelled"
    except JobDeferred as exc:
        db.rollback()
        try:
            q.defer(db, job.id, delay_seconds=exc.delay_seconds, worker=worker_id)
            db.commit()
            log.info("job %s#%s deferred for %.1fs", job.kind, job.id, exc.delay_seconds)
            return "deferred"
        except Exception as defer_err:
            db.rollback()
            log.warning("failed to defer job %s#%s: %s; failing retryable", job.kind, job.id, defer_err)
            q.fail(db, job.id, f"defer failed: {defer_err}", retryable=True, worker=worker_id)
            db.commit()
            return "failed"
    except Exception as exc:  # noqa: BLE001 - a handler failure is data, not a crash
        db.rollback()
        # A deliberate refusal (paused source, 404) must not burn five attempts.
        retryable = not drain and not isinstance(exc, NonRetryableJobError)
        q.fail(db, job.id, str(exc) or type(exc).__name__, retryable=retryable, worker=worker_id)
        db.commit()
        log.warning("job %s#%s failed (retryable=%s): %s", job.kind, job.id, retryable, exc)
        return "failed"


def _thread_loop(worker_id: str, drain: bool, kinds: set[str] | None) -> None:
    poll = get_settings().worker_poll_seconds
    db = SessionLocal()
    try:
        while not _stop.is_set():
            try:
                worked = _run_one(db, worker_id, drain, kinds)
            except Exception:  # noqa: BLE001 - a loop must survive a bad job
                db.rollback()
                log.exception("worker %s: unexpected error", worker_id)
                worked = False
            if not worked and not _stop.is_set():
                # Nothing to do: back off to the poll interval.
                _stop.wait(poll)
            db.expire_all()
    finally:
        db.close()


def _heartbeat_loop(ids: list[str]) -> None:
    """Keep `heartbeat_at` fresh for running jobs so the reaper leaves them alone."""
    while not _stop.wait(HEARTBEAT_SECONDS):
        db = SessionLocal()
        try:
            for wid in ids:
                db.execute(
                    text("UPDATE jobs SET heartbeat_at = now() WHERE status='running' AND locked_by = :wid"),
                    {"wid": wid},
                )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        finally:
            db.close()


def _reaper_loop() -> None:
    settings = get_settings()
    while not _stop.wait(60.0):
        db = SessionLocal()
        try:
            reaped = q.reap_stale(db, settings.job_stale_seconds)
            if reaped:
                db.commit()
                log.info("reaped %d stale job(s)", reaped)
        except Exception:  # noqa: BLE001
            db.rollback()
        finally:
            db.close()


def _touch_heartbeat() -> None:
    try:
        write_heartbeat()
    except OSError:
        log.warning("could not write worker heartbeat", exc_info=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="SourceHero job worker")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="process due jobs, then exit")
    parser.add_argument("--drain", action="store_true", help="ignore run_after; run until the queue is empty")
    parser.add_argument("--kinds", default=None, help="comma-separated kinds to accept")
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 if DB and heartbeat are healthy")
    args = parser.parse_args()

    configure_logging()
    if args.healthcheck:
        return healthcheck()
    # Importing the handler modules is what populates the registry; without this the
    # worker would claim every job as an unknown kind.
    register_all()
    settings = get_settings()
    concurrency = args.concurrency or settings.worker_concurrency
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()} if args.kinds else None
    base_id = _worker_id()

    def _handle_signal(signum, _frame) -> None:  # noqa: ANN001
        log.info("received signal %s; finishing in-flight jobs", signum)
        _stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once or args.drain:
        db = SessionLocal()
        try:
            count = 0
            while not _stop.is_set():
                if not _run_one(db, base_id, drain=args.drain, kinds=kinds):
                    break
                count += 1
                if args.once:
                    break
            log.info("processed %d job(s)", count)
        finally:
            db.close()
        return 0

    ids = [f"{base_id}-{i}" for i in range(max(1, concurrency))]
    threads = [
        threading.Thread(target=_thread_loop, args=(wid, False, kinds), name=f"worker-{i}", daemon=True)
        for i, wid in enumerate(ids)
    ]
    threads.append(threading.Thread(target=_heartbeat_loop, args=(ids,), name="heartbeat", daemon=True))
    threads.append(threading.Thread(target=_reaper_loop, name="reaper", daemon=True))

    if settings.scheduler_tick_seconds > 0:
        from app.jobs.scheduler import scheduler_loop

        threads.append(threading.Thread(target=scheduler_loop, name="scheduler", daemon=True))

    for t in threads:
        t.start()
    _touch_heartbeat()
    log.info("worker started: concurrency=%s kinds=%s", concurrency, kinds or "all")

    try:
        # The main thread is the heartbeat writer: it keeps ticking while handlers run.
        while not _stop.is_set():
            _stop.wait(1.0)
            _touch_heartbeat()
    except KeyboardInterrupt:
        _stop.set()
    finally:
        deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
        for t in threads:
            remaining = max(0.0, deadline - time.monotonic())
            t.join(timeout=remaining)
        from app.llm.providers.base import close_shared_http_client

        close_shared_http_client()
        log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
