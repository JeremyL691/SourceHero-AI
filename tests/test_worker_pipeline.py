"""Tests for H2.1 - H2.4: worker transaction model, cancel & progress, advisory lock, and ingestion run persistence."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import time as clock
from app.jobs import queue as q
from app.jobs import worker as worker_mod
from app.jobs.registry import (
    JobCancelled,
    JobContext,
    JobDeferred,
    get_handler,
    job_handler,
)
from app.models.job import Job

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _manual_queue(no_inline_jobs):
    """Ensure worker does not run inline during these tests and handlers are registered."""
    worker_mod.register_all()


def test_worker_failing_handler_increments_attempts_and_enters_dead(fresh_db, engine):
    """DATA-2 regression test:

    A failing handler must increment attempts across worker iterations (1, 2, ..., max_attempts)
    even when the handler transaction rolls back, eventually transitioning to 'dead' with backoff.
    """
    call_count = 0

    @job_handler("test_always_explodes")
    def explodes(ctx: JobContext, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"boom #{call_count}")

    job = q.enqueue(fresh_db, "test_always_explodes", {}, max_attempts=3)
    fresh_db.commit()

    # Iteration 1
    ran = worker_mod._run_one(fresh_db, "w1", drain=False, kinds=None)
    assert ran is True
    fresh_db.expire_all()
    j1 = fresh_db.get(Job, job.id)
    assert j1.attempts == 1
    assert j1.status == "queued"
    assert "boom #1" in (j1.last_error or "")

    # Reset run_after so it's due immediately for iteration 2
    fresh_db.execute(
        text("UPDATE jobs SET run_after = now() - interval '1 second' WHERE id = :id"),
        {"id": job.id},
    )
    fresh_db.commit()

    # Iteration 2
    ran = worker_mod._run_one(fresh_db, "w1", drain=False, kinds=None)
    assert ran is True
    fresh_db.expire_all()
    j2 = fresh_db.get(Job, job.id)
    assert j2.attempts == 2
    assert j2.status == "queued"
    assert "boom #2" in (j2.last_error or "")

    # Reset run_after so it's due immediately for iteration 3 (max_attempts reached)
    fresh_db.execute(
        text("UPDATE jobs SET run_after = now() - interval '1 second' WHERE id = :id"),
        {"id": job.id},
    )
    fresh_db.commit()

    # Iteration 3
    ran = worker_mod._run_one(fresh_db, "w1", drain=False, kinds=None)
    assert ran is True
    fresh_db.expire_all()
    j3 = fresh_db.get(Job, job.id)
    assert j3.attempts == 3
    assert j3.status == "dead"
    assert "boom #3" in (j3.last_error or "")


def test_enqueue_same_dedupe_key_while_running_does_not_block(fresh_db, engine):
    """DATA-3 regression test:

    While a job is running, enqueuing with the same dedupe_key must NOT block
    under lock_timeout='2s', and must queue a second job.
    """
    job_started = threading.Event()
    job_can_finish = threading.Event()

    @job_handler("test_long_runner_dedupe")
    def long_runner(ctx: JobContext, payload: dict) -> dict:
        job_started.set()
        job_can_finish.wait(timeout=5.0)
        return {"done": True}

    job1 = q.enqueue(fresh_db, "test_long_runner_dedupe", {"n": 1}, dedupe_key="dk:test")
    fresh_db.commit()

    # Run job1 in background thread using worker_mod._run_one
    def _worker():
        s = Session(bind=engine)
        try:
            worker_mod._run_one(s, "w-test", drain=False, kinds=None)
        finally:
            s.close()

    t = threading.Thread(target=_worker)
    t.start()

    try:
        assert job_started.wait(timeout=3.0), "Worker did not start job in time"

        # Now in fresh_db, try to enqueue with lock_timeout = 2s
        fresh_db.execute(text("SET lock_timeout = '2000ms'"))
        job2 = q.enqueue(fresh_db, "test_long_runner_dedupe", {"n": 2}, dedupe_key="dk:test")
        fresh_db.commit()

        assert job2.id != job1.id
        assert job2.status == "queued"
    finally:
        job_can_finish.set()
        t.join(timeout=5.0)


def test_cancel_running_job_halts_and_marks_cancelled(fresh_db, engine):
    """DATA-3 regression test:

    Cancelling a running job must allow handler to detect cancellation via check_cancelled(),
    and the final job status in the DB must be 'cancelled' (not 'running').
    """
    started = threading.Event()
    cancelled_seen = threading.Event()

    @job_handler("test_cancellable_job")
    def cancellable(ctx: JobContext, payload: dict) -> dict:
        started.set()
        for _ in range(50):
            time.sleep(0.05)
            try:
                ctx.check_cancelled()
            except JobCancelled:
                cancelled_seen.set()
                raise
        return {"done": True}

    job = q.enqueue(fresh_db, "test_cancellable_job", {})
    fresh_db.commit()

    def _worker():
        s = Session(bind=engine)
        try:
            worker_mod._run_one(s, "w-cancel", drain=False, kinds=None)
        finally:
            s.close()

    t = threading.Thread(target=_worker)
    t.start()

    try:
        assert started.wait(timeout=3.0), "Job did not start in time"

        # Ask to cancel
        q.cancel(fresh_db, job.id)
        fresh_db.commit()

        assert cancelled_seen.wait(timeout=3.0), "Handler did not observe JobCancelled"
        t.join(timeout=5.0)

        fresh_db.expire_all()
        j = fresh_db.get(Job, job.id)
        assert j.status == "cancelled"
    finally:
        if t.is_alive():
            t.join(timeout=1.0)


def test_kinds_filter_in_dequeue_does_not_consume_attempts(fresh_db):
    """DATA-16 regression test:

    Worker with kinds={"specific_kind"} must not dequeue or fail/retry jobs of other kinds.
    Other kinds remain with attempts == 0.
    """
    j_other = q.enqueue(fresh_db, "other_kind", {})
    j_mine = q.enqueue(fresh_db, "my_kind", {})
    fresh_db.commit()

    @job_handler("my_kind")
    def handle_my(ctx: JobContext, payload: dict) -> dict:
        return {"ok": True}

    ran = worker_mod._run_one(fresh_db, "w-kinds", drain=False, kinds={"my_kind"})
    assert ran is True

    fresh_db.expire_all()
    j_mine_db = fresh_db.get(Job, j_mine.id)
    assert j_mine_db.status == "succeeded"

    j_other_db = fresh_db.get(Job, j_other.id)
    assert j_other_db.status == "queued"
    assert j_other_db.attempts == 0


def test_progress_committed_in_independent_session_visible_externally(fresh_db, engine):
    """DATA-3 / H2.2 regression test:

    ctx.progress() commits via an independent session, making partial progress immediately
    visible to other database connections while the handler is still executing.
    """
    progress_saved = threading.Event()
    can_finish = threading.Event()

    @job_handler("test_progress_reporter")
    def reporter(ctx: JobContext, payload: dict) -> dict:
        ctx.progress({"step": 1, "pct": 25})
        progress_saved.set()
        can_finish.wait(timeout=5.0)
        return {"done": True}

    job = q.enqueue(fresh_db, "test_progress_reporter", {})
    fresh_db.commit()

    def _worker():
        s = Session(bind=engine)
        try:
            worker_mod._run_one(s, "w-prog", drain=False, kinds=None)
        finally:
            s.close()

    t = threading.Thread(target=_worker)
    t.start()

    try:
        assert progress_saved.wait(timeout=3.0), "Progress not reported in time"

        # Read from fresh_db (separate connection)
        other_session = Session(bind=engine)
        try:
            j = other_session.get(Job, job.id)
            assert j is not None
            assert (j.result or {}).get("progress") == {"step": 1, "pct": 25}
        finally:
            other_session.close()
    finally:
        can_finish.set()
        t.join(timeout=5.0)


def test_advisory_xact_lock_and_job_deferred(fresh_db, engine):
    """DATA-4 / H2.3 regression test:

    Two transactions for same lock key cannot run simultaneously.
    Raising JobDeferred(delay_seconds=10) requeues job with run_after and does NOT consume attempts.
    """
    from app.services.advisory import try_xact_lock

    call_count = 0

    @job_handler("test_lock_contention")
    def locked_handler(ctx: JobContext, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        key = f"claims:{payload['user_id']}"
        if not try_xact_lock(ctx.db, key):
            raise JobDeferred(delay_seconds=10.0)
        return {"locked": True}

    uid = uuid.uuid4()
    job = q.enqueue(fresh_db, "test_lock_contention", {"user_id": str(uid)})
    fresh_db.commit()

    # Open connection 2 and hold transaction lock
    conn2 = engine.connect()
    trans2 = conn2.begin()
    try:
        # Take transaction lock on conn2
        taken = conn2.execute(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:name))"),
            {"name": f"claims:{uid}"},
        ).scalar()
        assert taken is True

        # Now worker tries to run the job
        ran = worker_mod._run_one(fresh_db, "w-lock", drain=False, kinds=None)
        assert ran is True

        fresh_db.expire_all()
        j = fresh_db.get(Job, job.id)
        assert j.status == "queued"
        # attempts must NOT have been consumed (attempts == 0)
        assert j.attempts == 0
        assert j.run_after > clock.utcnow() + timedelta(seconds=8)

        # Release conn2 lock
        trans2.commit()
    finally:
        conn2.close()

    # Now that conn2 released the lock, verify lock can be taken on conn1 without lingering
    assert try_xact_lock(fresh_db, f"claims:{uid}") is True
    fresh_db.commit()


def test_duplicate_job_handler_registration_fails():
    """DATA-20 regression test:

    Registering a handler for an already-registered job kind must raise an error.
    """

    @job_handler("test_unique_kind")
    def fn1(ctx, p):
        return {}

    with pytest.raises(ValueError, match="already registered"):

        @job_handler("test_unique_kind")
        def fn2(ctx, p):
            return {}


def test_failed_ingestion_run_persisted(fresh_db, monkeypatch):
    """DATA-14 regression test:

    When ingestion fails, an IngestionRun with status='failed' and sanitized error must be persisted,
    and source.status must be 'failed'.
    """
    from app.ingestion.fetch import FetchError
    from app.models.chunk import IngestionRun
    from app.models.source import Source

    uid = uuid.uuid4()
    source = Source(
        id=uuid.uuid4(),
        user_id=uid,
        name="test source",
        source_type="webpage",
        url="https://example.com/fail",
        status="active",
    )
    fresh_db.add(source)
    fresh_db.commit()

    def mock_fetch(url, **kwargs):
        raise FetchError("simulated connection failure", code="connection_failed")

    monkeypatch.setattr("app.ingestion.fetch.fetch", mock_fetch)

    q.enqueue(fresh_db, "ingest_source", {"source_id": str(source.id)})
    fresh_db.commit()

    ran = worker_mod._run_one(fresh_db, "w-fail", drain=False, kinds=None)
    assert ran is True

    fresh_db.expire_all()
    runs = fresh_db.query(IngestionRun).filter_by(source_id=source.id).all()
    assert len(runs) >= 1
    assert runs[0].status == "failed"
    assert runs[0].error is not None

    src = fresh_db.get(Source, source.id)
    assert src.status == "failed"


def test_refresh_source_handler_is_real_implementation():
    """DATA-20 regression test:

    refresh_source handler must be the real implementation from pipeline.py, not a stub.
    """
    worker_mod.register_all()
    handler = get_handler("refresh_source")
    assert handler.__module__ == "app.ingestion.pipeline"


def _make_fresh_client(fresh_db, email="uploader@test.local", password="test-password-123"):
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: fresh_db
    client = TestClient(app)
    resp = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
    return client


def test_identity_key_pdf_and_text_file_upload(pdf_two_pages, tmp_path, monkeypatch, fresh_db):
    """DATA-1 regression test:

    - Same user uploads a.pdf and b.md -> creates 2 documents, both searchable.
    - Repeat upload of a.pdf -> marked unchanged (no duplicate document/version).
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    client = _make_fresh_client(fresh_db, email="uploader@test.local")

    # 1. Upload a.pdf
    res_a = client.post(
        "/v1/sources/upload",
        files={"file": ("a.pdf", pdf_two_pages, "application/pdf")},
    )
    assert res_a.status_code == 201

    # 2. Upload b.md
    res_b = client.post(
        "/v1/sources/upload",
        files={"file": ("b.md", b"# Markdown doc\n\nUnique quantum battery breakthrough.", "text/markdown")},
    )
    assert res_b.status_code == 201

    # Drain worker queue
    while worker_mod._run_one(fresh_db, "test-w", drain=True, kinds=None):
        pass

    # Both documents should exist
    docs = client.get("/v1/documents").json()
    assert len(docs) == 2

    # Search for PDF content
    search_pdf = client.post("/v1/search", json={"query": "Halcyon Cells press release"}).json()
    assert len(search_pdf["hits"]) >= 1

    # Search for MD content
    search_md = client.post("/v1/search", json={"query": "quantum battery breakthrough"}).json()
    assert len(search_md["hits"]) >= 1

    # Repeat upload of a.pdf -> marked unchanged
    res_a2 = client.post(
        "/v1/sources/upload",
        files={"file": ("a.pdf", pdf_two_pages, "application/pdf")},
    )
    assert res_a2.status_code == 201
    while worker_mod._run_one(fresh_db, "test-w", drain=True, kinds=None):
        pass

    # Documents count should still be 2
    docs_after = client.get("/v1/documents").json()
    assert len(docs_after) == 2


def test_post_captures_text_note_searchable(fresh_db):
    """DATA-5 regression test:

    POST /captures with text note is indexed and searchable via /v1/search.
    """
    client = _make_fresh_client(fresh_db, email="notecapturer@test.local")
    resp = client.post(
        "/v1/captures",
        json={
            "text": "Superconducting nanowires operate at sub-Kelvin temperatures.",
            "title": "Nanowire Note",
        },
    )
    assert resp.status_code == 201
    while worker_mod._run_one(fresh_db, "test-w", drain=True, kinds=None):
        pass

    search_res = client.post("/v1/search", json={"query": "Superconducting nanowires"}).json()
    assert len(search_res["hits"]) >= 1
    assert any("Superconducting nanowires" in h["text"] for h in search_res["hits"])
