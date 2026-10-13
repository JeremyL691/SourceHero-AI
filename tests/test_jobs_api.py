"""The /v1/jobs endpoints, and the worker's --once mode."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.jobs import queue as q
from app.models.job import Job

pytestmark = pytest.mark.db


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def authed(make_client):
    return make_client(email=f"jobs{uuid.uuid4().hex[:10]}@test.local")


def _owner_id(client) -> uuid.UUID:
    return uuid.UUID(client.get("/v1/auth/me").json()["id"])


def _make_job(db, user_id: uuid.UUID, **kwargs) -> Job:
    defaults = {"kind": "test_kind", "payload": {}, "user_id": user_id}
    return q.enqueue(db, **{**defaults, **kwargs})


def test_list_jobs_only_returns_your_own(authed, db, no_inline_jobs):
    mine = _make_job(db, _owner_id(authed))
    _make_job(db, uuid.uuid4())
    db.commit()

    ids = [j["id"] for j in authed.get("/v1/jobs").json()]
    assert ids == [mine.id]


def test_job_detail_is_scoped(authed, db, no_inline_jobs):
    theirs = _make_job(db, uuid.uuid4())
    db.commit()
    # 404, not 403: a 403 would confirm the id exists.
    assert authed.get(f"/v1/jobs/{theirs.id}").status_code == 404


def test_system_jobs_are_visible_to_admins_only(authed, db, no_inline_jobs):
    system = _make_job(db, None, kind="maintenance")
    db.commit()
    # A plain account cannot see user_id IS NULL jobs.
    assert authed.get("/v1/jobs").json() == []

    from app.models.user import User

    user = db.execute(select(User).where(User.id == _owner_id(authed))).scalar_one()
    user.is_admin = True
    db.commit()
    assert system.id in [j["id"] for j in authed.get("/v1/jobs").json()]


def test_filters(authed, db, no_inline_jobs):
    uid = _owner_id(authed)
    a = _make_job(db, uid, kind="alpha")
    b = _make_job(db, uid, kind="beta")
    db.commit()
    assert [j["id"] for j in authed.get("/v1/jobs", params={"kind": "alpha"}).json()] == [a.id]
    # Newest first.
    assert [j["id"] for j in authed.get("/v1/jobs", params={"status": "queued"}).json()] == [b.id, a.id]
    assert authed.get("/v1/jobs", params={"status": "succeeded"}).json() == []


def test_retry_and_cancel(authed, db, no_inline_jobs):
    uid = _owner_id(authed)
    job = _make_job(db, uid, max_attempts=1)
    db.commit()

    claimed = q.dequeue(db, worker="w")
    q.fail(db, claimed.id, "boom")
    db.commit()

    resp = authed.post(f"/v1/jobs/{job.id}/retry")
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "queued"

    cancelled = authed.post(f"/v1/jobs/{job.id}/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json()["cancelled"] is True


def test_retry_of_a_queued_job_is_rejected(authed, db, no_inline_jobs):
    job = _make_job(db, _owner_id(authed))
    db.commit()
    assert authed.post(f"/v1/jobs/{job.id}/retry").status_code == 404


def test_cannot_touch_another_users_job(authed, db, no_inline_jobs):
    theirs = _make_job(db, uuid.uuid4())
    db.commit()
    assert authed.get(f"/v1/jobs/{theirs.id}").status_code == 404
    assert authed.post(f"/v1/jobs/{theirs.id}/cancel").status_code == 404
    assert authed.post(f"/v1/jobs/{theirs.id}/retry").status_code == 404


def test_reindex_requires_admin(authed, db, no_inline_jobs):
    from app.models.user import User

    assert authed.post("/v1/index/reindex").status_code == 403

    user = db.execute(select(User).where(User.id == _owner_id(authed))).scalar_one()
    user.is_admin = True
    db.commit()
    resp = authed.post("/v1/index/reindex")
    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"]


def test_worker_once_runs_a_queued_job_to_success(fresh_db):
    """Manual acceptance check, as a test.

    Enqueue a job whose handler the worker subprocess actually has registered
    (`maintenance`), run `python -m app.jobs.worker --once`, and assert the row ends
    up `succeeded`.
    """
    from app.core.config import get_settings
    from tests.conftest import TEST_DB_URL

    # A subprocess cannot see rows inside the `db` fixture's rolled-back
    # transaction, so this test uses fresh_db (which really commits).
    db = fresh_db
    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    try:
        job = q.enqueue(db, "maintenance", {})
        db.commit()
    finally:
        settings.jobs_inline = original

    proc = subprocess.run(
        [sys.executable, "-m", "app.jobs.worker", "--once"],
        capture_output=True,
        text=True,
        env={
            "DATABASE_URL": TEST_DB_URL,
            "SOURCEHERO_ENV": "test",
            "LLM_PROVIDER": "fake",
            "EMBEDDING_PROVIDER": "hashing",
            "JOBS_INLINE": "false",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
        },
        cwd=str(_repo_root()),
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]

    db.expire_all()
    db.refresh(job)
    assert job.status == "succeeded", f"status={job.status} error={job.last_error}"
    assert job.attempts == 1
    assert "reaped" in (job.result or {})


def test_worker_once_exits_when_the_queue_is_empty(fresh_db):
    """`--once` must not hang on an empty queue."""
    from tests.conftest import TEST_DB_URL

    proc = subprocess.run(
        [sys.executable, "-m", "app.jobs.worker", "--once"],
        capture_output=True,
        text=True,
        env={
            "DATABASE_URL": TEST_DB_URL,
            "SOURCEHERO_ENV": "test",
            "JOBS_INLINE": "false",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(Path.home()),
        },
        cwd=str(_repo_root()),
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "processed 0 job" in proc.stderr
