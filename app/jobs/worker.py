"""The worker process: N threads pulling from the queue, plus a heartbeat and a
scheduler tick.

    python -m app.jobs.worker [--concurrency N] [--once] [--drain] [--kinds a,b]

`--once`  process whatever is due, then exit.
`--drain` ignore `run_after` entirely and keep going until the queue is empty,
          including jobs enqueued while draining. Used by `make demo`, scripts and
          tests so that debounced follow-ups (plan_topics, compile_wiki_page) finish
          before the process exits.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import threading
import time
import uuid

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
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
# Runaway guard for `--drain`. Reaching it means a handler keeps enqueueing work.
MAX_DRAIN_JOBS = 10_000
SHUTDOWN_GRACE_SECONDS = 30.0

_stop = threading.Event()


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def _run_one(db, worker_id: str, drain: bool, kinds: set[str] | None) -> bool:
    """Claim and execute one job. Returns True when work was done.

    In drain mode a failure is terminal. Drain ignores `run_after`, so a job requeued
    with backoff would be claimed again on the very next iteration and a permanently
    failing job would spin forever instead of failing once.
    """
    job = q.dequeue(db, worker=worker_id, drain=drain)
    if job is None:
        return False
    if kinds and job.kind not in kinds:
        # Claimed but not ours: hand it straight back rather than failing it.
        q.fail(db, job.id, f"kind {job.kind} not enabled on this worker", retryable=True)
        db.commit()
        return False

    ctx_user = job.user_id
    from app.jobs.registry import JobContext

    started = time.monotonic()
    try:
        handler = get_handler(job.kind)
    except UnknownJobKind as exc:
        q.fail(db, job.id, str(exc), retryable=False)
        db.commit()
        log.warning("unknown job kind %s (id=%s)", job.kind, job.id)
        return True

    try:
        result = handler(JobContext(db=db, job=job, user_id=ctx_user, logger=log), dict(job.payload or {}))
        q.complete(db, job.id, result or {})
        db.commit()
        log.info("job %s#%s ok in %.2fs", job.kind, job.id, time.monotonic() - started)
    except JobCancelled:
        q.cancel(db, job.id)
        db.commit()
        log.info("job %s#%s cancelled", job.kind, job.id)
    except Exception as exc:  # noqa: BLE001 - a handler failure is data, not a crash
        db.rollback()
        # A deliberate refusal (paused source, 404) must not burn five attempts.
        retryable = not drain and not isinstance(exc, NonRetryableJobError)
        q.fail(db, job.id, str(exc) or type(exc).__name__, retryable=retryable)
        db.commit()
        log.warning("job %s#%s failed (retryable=%s): %s", job.kind, job.id, retryable, exc)
    return True


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
            from sqlalchemy import text

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


def main() -> int:
    parser = argparse.ArgumentParser(description="SourceHero job worker")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--once", action="store_true", help="process due jobs, then exit")
    parser.add_argument("--drain", action="store_true", help="ignore run_after; run until the queue is empty")
    parser.add_argument("--kinds", default=None, help="comma-separated kinds to accept")
    args = parser.parse_args()

    configure_logging()
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
    log.info("worker started: concurrency=%s kinds=%s", concurrency, kinds or "all")

    try:
        while not _stop.is_set():
            _stop.wait(1.0)
    except KeyboardInterrupt:
        _stop.set()

    deadline = time.monotonic() + SHUTDOWN_GRACE_SECONDS
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
