"""M3.5: the queue's ordering, dedupe, backoff, reaping and concurrency guarantees."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core import time as clock
from app.jobs import queue as q
from app.jobs.registry import JobContext, UnknownJobKind, job_handler, run_inline

pytestmark = pytest.mark.db

NOW = "2026-06-01T12:00:00+00:00"


@pytest.fixture
def t0():
    from datetime import UTC, datetime

    return datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _manual_queue(no_inline_jobs):
    """Every test in this file drives enqueue/dequeue explicitly."""


def _uid() -> uuid.UUID:
    return uuid.uuid4()


# --- enqueue / dequeue ordering -----------------------------------------


def test_dequeue_respects_priority_then_run_after(fresh_db, t0):
    db = fresh_db
    q.enqueue(db, "low", {}, now=t0)
    q.enqueue(db, "high", {}, priority=10, now=t0)
    q.enqueue(db, "later", {}, run_after=t0 + timedelta(hours=1), now=t0)
    db.commit()

    # A future job is invisible until it is due.
    first = q.dequeue(db, worker="w1", now=t0)
    assert first.kind == "high"
    second = q.dequeue(db, worker="w2", now=t0)
    assert second.kind == "low"
    assert q.dequeue(db, worker="w3", now=t0) is None

    third = q.dequeue(db, worker="w4", now=t0 + timedelta(hours=2))
    assert third.kind == "later"


def test_drain_ignores_run_after(fresh_db, t0):
    db = fresh_db
    q.enqueue(db, "delayed", {}, run_after=t0 + timedelta(days=7), now=t0)
    db.commit()
    assert q.dequeue(db, worker="w", now=t0) is None
    assert q.dequeue(db, worker="w", now=t0, drain=True).kind == "delayed"


def test_fifo_within_equal_priority(fresh_db, t0):
    db = fresh_db
    a = q.enqueue(db, "a", {}, now=t0)
    b = q.enqueue(db, "b", {}, now=t0)
    db.commit()
    assert q.dequeue(db, worker="w", now=t0).id == a.id
    assert q.dequeue(db, worker="w", now=t0).id == b.id


# --- dedupe --------------------------------------------------------------


def test_dedupe_merges_while_queued(fresh_db, t0):
    db = fresh_db
    first = q.enqueue(db, "kind", {}, dedupe_key="k:1", now=t0)
    second = q.enqueue(db, "kind", {}, dedupe_key="k:1", now=t0)
    db.commit()
    assert first.id == second.id
    assert q.pending(db, now=t0) == 1


def test_dedupe_allows_a_second_job_while_the_first_runs(fresh_db, t0):
    """A change that lands mid-run must not be swallowed by the running job."""
    db = fresh_db
    first = q.enqueue(db, "kind", {"n": 1}, dedupe_key="k:1", now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    assert claimed.id == first.id

    second = q.enqueue(db, "kind", {"n": 2}, dedupe_key="k:1", now=t0)
    db.commit()
    assert second.id != first.id
    assert second.status == "queued"


def test_dedupe_key_is_reusable_once_the_job_finishes(fresh_db, t0):
    db = fresh_db
    first = q.enqueue(db, "kind", {}, dedupe_key="k:1", now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    q.complete(db, claimed.id, {"ok": True}, now=t0)
    db.commit()
    assert q.pending(db, now=t0) == 0

    second = q.enqueue(db, "kind", {}, dedupe_key="k:1", now=t0)
    db.commit()
    assert second.id != first.id


# --- concurrency ---------------------------------------------------------


def test_two_connections_never_claim_the_same_job(fresh_db, engine, t0):
    """The whole point of `FOR UPDATE SKIP LOCKED`."""
    from sqlalchemy.orm import Session

    db = fresh_db
    for i in range(6):
        q.enqueue(db, f"job{i}", {}, now=t0)
    db.commit()

    a = Session(bind=engine)
    b = Session(bind=engine)
    try:
        claimed_a = q.dequeue(a, worker="worker-a", now=t0)
        claimed_b = q.dequeue(b, worker="worker-b", now=t0)
        assert claimed_a is not None and claimed_b is not None
        assert claimed_a.id != claimed_b.id
    finally:
        a.close()
        b.close()


# --- failure and backoff -------------------------------------------------


def test_backoff_is_exponential_capped_and_jittered():
    values = [q.backoff_seconds(n) for n in range(1, 12)]
    for n, v in zip(range(1, 12), values, strict=True):
        base = min(2**n * 10, 1800)
        assert base <= v <= base + 5, f"attempt {n}: {v} outside [{base}, {base + 5}]"
    assert q.backoff_seconds(50) <= 1805


def test_failure_requeues_with_backoff_until_max_attempts(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, max_attempts=3, now=t0)
    db.commit()

    for attempt in (1, 2):
        claimed = q.dequeue(db, worker="w", now=t0)
        q.fail(db, claimed.id, "boom", now=t0)
        db.commit()
        db.refresh(job)
        assert job.status == "queued"
        assert job.attempts == attempt
        assert job.run_after > t0, "backoff must push run_after into the future"
        # A retried job is invisible until its backoff expires.
        assert q.dequeue(db, worker="w", now=t0) is None
        # ...and the next attempt starts from the retry moment, not from t0.
        t0 = job.run_after

    claimed = q.dequeue(db, worker="w", now=t0)
    assert claimed is not None
    q.fail(db, claimed.id, "boom", now=t0)
    db.commit()
    db.refresh(job)
    assert job.status == "dead"
    assert job.last_error == "boom"


def test_non_retryable_failure_goes_straight_to_failed(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    q.fail(db, claimed.id, "bad request", retryable=False, now=t0)
    db.commit()
    db.refresh(job)
    assert job.status == "failed"


def test_last_error_is_truncated(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, max_attempts=1, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    q.fail(db, claimed.id, "x" * 5000, now=t0)
    db.commit()
    db.refresh(job)
    assert len(job.last_error) == 2000


# --- reaper / cancel / retry --------------------------------------------


def test_reaper_returns_stale_running_jobs(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="dead-worker", now=t0)
    assert claimed.status == "running"

    # Not yet stale.
    assert q.reap_stale(db, 300, now=t0 + timedelta(seconds=10)) == 0
    # Heartbeat fresh -> still ours.
    q.heartbeat(db, job.id, now=t0 + timedelta(seconds=10))
    assert q.reap_stale(db, 300, now=t0 + timedelta(seconds=20)) == 0
    # Worker died.
    assert q.reap_stale(db, 300, now=t0 + timedelta(seconds=1000)) == 1
    db.commit()
    db.refresh(job)
    assert job.status == "queued"
    assert job.locked_by is None


def test_cancel_queued_vs_running(fresh_db, t0):
    db = fresh_db
    first = q.enqueue(db, "a", {}, now=t0)
    second = q.enqueue(db, "b", {}, now=t0)
    db.commit()

    # FIFO: `first` is the one that gets claimed and is now running.
    claimed = q.dequeue(db, worker="w", now=t0)
    assert claimed.id == first.id

    assert q.cancel(db, second.id) is True, "a queued job cancels outright"
    assert q.cancel(db, first.id) is False, "a running job can only be asked to stop"
    db.commit()
    db.refresh(first)
    db.refresh(second)
    assert first.payload.get("_cancel") is True
    assert second.status == "cancelled"


def test_retry_resets_attempts(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, max_attempts=1, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    q.fail(db, claimed.id, "boom", now=t0)
    db.commit()

    assert q.retry(db, job.id, now=t0) is True
    db.commit()
    db.refresh(job)
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.last_error is None
    # Queued jobs are not retryable.
    assert q.retry(db, job.id, now=t0) is False


def test_complete_records_the_result(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    q.complete(db, claimed.id, {"chunks": 12}, now=t0)
    db.commit()
    db.refresh(job)
    assert job.status == "succeeded"
    assert job.result == {"chunks": 12}
    assert job.finished_at is not None


# --- registry ------------------------------------------------------------


def test_handler_is_invoked_and_result_recorded(fresh_db, t0):
    calls: list[dict] = []

    @job_handler("test_echo")
    def echo(ctx: JobContext, payload: dict) -> dict:
        calls.append(payload)
        return {"echoed": payload.get("value")}

    db = fresh_db
    q.enqueue(db, "test_echo", {"value": 7}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    from app.jobs.registry import get_handler

    assert get_handler("test_echo") is echo
    result = get_handler("test_echo")(JobContext(db=db, job=claimed, user_id=None), dict(claimed.payload))
    q.complete(db, claimed.id, result, now=t0)
    db.commit()
    assert calls == [{"value": 7}]


def test_unknown_kind_fails_without_retrying(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "no_such_kind", {}, max_attempts=5, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    with pytest.raises(UnknownJobKind):
        run_inline(db, claimed, now=t0)
    db.commit()
    db.refresh(job)
    assert job.status == "failed"


def test_run_inline_reports_failure_and_rethrows(fresh_db, t0):
    @job_handler("test_boom")
    def boom(ctx: JobContext, payload: dict) -> dict:
        raise RuntimeError("handler exploded")

    db = fresh_db
    job = q.enqueue(db, "test_boom", {}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)

    with pytest.raises(RuntimeError, match="handler exploded"):
        run_inline(db, claimed, now=t0)
    db.commit()
    db.refresh(job)
    # inline failures are terminal so a test cannot spin forever
    assert job.status == "failed"
    assert "handler exploded" in job.last_error


def test_ctx_enqueue_links_to_parent_and_carries_user(fresh_db, t0):
    db = fresh_db
    uid = _uid()
    parent = q.enqueue(db, "parent", {}, user_id=uid, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    ctx = JobContext(db=db, job=claimed, user_id=claimed.user_id)
    child = ctx.enqueue("child", {"k": 1}, now=t0)
    db.commit()
    assert child.parent_job_id == parent.id
    assert child.user_id == uid


def test_check_cancelled_raises_once_the_flag_is_set(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    ctx = JobContext(db=db, job=claimed, user_id=None)
    ctx.check_cancelled()  # no flag yet

    q.cancel(db, job.id)
    db.commit()
    from app.jobs.registry import JobCancelled

    with pytest.raises(JobCancelled):
        ctx.check_cancelled()


def test_progress_is_merged_into_result(fresh_db, t0):
    db = fresh_db
    job = q.enqueue(db, "kind", {}, now=t0)
    db.commit()
    claimed = q.dequeue(db, worker="w", now=t0)
    ctx = JobContext(db=db, job=claimed, user_id=None)
    ctx.progress({"seen": 1})
    ctx.progress({"written": 3})
    db.commit()
    db.refresh(job)
    assert job.result["progress"] == {"seen": 1, "written": 3}


def test_pending_counts_only_due_jobs(fresh_db, t0):
    db = fresh_db
    q.enqueue(db, "due", {}, now=t0)
    q.enqueue(db, "future", {}, run_after=t0 + timedelta(days=1), now=t0)
    db.commit()
    assert q.pending(db, now=t0) == 1
    assert q.pending(db, now=t0, drain=True) == 2
    assert q.pending(db, now=t0, user_id=_uid()) == 0


def test_job_rows_are_visible_to_their_owner_only(make_client, db):
    """A job belongs to the user who caused it, not to whoever asks."""
    from uuid import UUID

    other = make_client(email=f"jobs{uuid.uuid4().hex[:10]}@test.local")
    owner = UUID(other.get("/v1/auth/me").json()["id"])

    theirs = q.enqueue(db, "kind", {}, user_id=uuid.uuid4(), now=clock.utcnow())
    db.commit()
    assert theirs.id not in [j["id"] for j in other.get("/v1/jobs").json()]

    mine = q.enqueue(db, "kind", {}, user_id=owner, now=clock.utcnow())
    db.commit()
    assert mine.id in [j["id"] for j in other.get("/v1/jobs").json()]


def test_drain_does_not_loop_on_a_failing_job(fresh_db, monkeypatch):
    """`--drain` ignores `run_after`, so a requeued failure would spin forever.

    Regression guard: a permanently failing job must fail once and let the drain
    terminate, not be re-claimed on the next iteration.
    """
    from app.jobs import worker as worker_module

    attempts = {"n": 0}

    @job_handler("test_always_fails")
    def always_fails(ctx, payload):
        attempts["n"] += 1
        raise RuntimeError("permanently broken")

    worker_module.register_all()
    job = q.enqueue(fresh_db, "test_always_fails", {})
    fresh_db.commit()

    ran = worker_module._run_one(fresh_db, "w", drain=True, kinds=None)
    assert ran
    assert attempts["n"] == 1, "the handler must run exactly once in drain mode"
    fresh_db.refresh(job)
    assert job.status == "failed", "a drain failure is terminal, not retried"
    assert job.run_after <= job.finished_at or job.finished_at is not None

    # The queue is now empty of this kind, so a second pass does nothing.
    assert not worker_module._run_one(fresh_db, "w", drain=True, kinds=None)
