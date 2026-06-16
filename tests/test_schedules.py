from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.conftest import TEST_USER_ID
from app.database import Base, SessionLocal, engine
from app.models import ScheduledJob, Source
from app.services.schedules import create_schedule, run_due_jobs_once, run_schedule_now


def _seed_source(db, *, status: str = "active") -> Source:
    source = Source(user_id=TEST_USER_ID, source_type="webpage", name="Scheduled Source", url="https://example.com/source", status=status)
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def test_run_schedule_now_ingests_source(monkeypatch, db_session):
    source = _seed_source(db_session)
    monkeypatch.setattr(
        "app.services.schedules.ingest_source",
        lambda db, user_id, source_id: SimpleNamespace(status="success", documents_inserted=1, chunks_inserted=2),
    )
    job = create_schedule(
        db_session,
        TEST_USER_ID,
        job_type="ingest_source",
        name="Daily ingest",
        schedule_kind="daily",
        time_local="09:00",
        day_of_week=None,
        payload={"source_id": source.id},
    )

    run = run_schedule_now(db_session, TEST_USER_ID, job.id)

    assert run.status == "success"
    assert "chunks=2" in (run.summary or "")


def test_paused_source_schedule_fails_without_overwriting_source_status(db_session):
    source = _seed_source(db_session, status="paused")
    job = create_schedule(
        db_session,
        TEST_USER_ID,
        job_type="ingest_source",
        name="Paused ingest",
        schedule_kind="daily",
        time_local="09:00",
        day_of_week=None,
        payload={"source_id": source.id},
    )

    run = run_schedule_now(db_session, TEST_USER_ID, job.id)
    db_session.refresh(source)
    db_session.refresh(job)

    assert run.status == "failed"
    assert source.status == "paused"
    assert job.status == "active"
    assert "paused" in (job.last_error or "").lower()


def test_failed_ingest_marks_schedule_failed(monkeypatch, db_session):
    source = _seed_source(db_session)
    monkeypatch.setattr(
        "app.services.schedules.ingest_source",
        lambda db, user_id, source_id: SimpleNamespace(
            status="failed",
            documents_inserted=0,
            chunks_inserted=0,
            error_message="HTTP 403 — the site blocks automated readers.",
        ),
    )
    job = create_schedule(
        db_session,
        TEST_USER_ID,
        job_type="ingest_source",
        name="Failing ingest",
        schedule_kind="daily",
        time_local="09:00",
        day_of_week=None,
        payload={"source_id": source.id},
    )

    run = run_schedule_now(db_session, TEST_USER_ID, job.id)
    db_session.refresh(job)

    assert run.status == "failed"
    assert job.status == "active"
    assert "403" in (job.last_error or "")


def test_schedule_creation_rejects_unknown_source(db_session):
    with pytest.raises(ValueError):
        create_schedule(
            db_session,
            TEST_USER_ID,
            job_type="ingest_source",
            name="Bad source",
            schedule_kind="daily",
            time_local="09:00",
            day_of_week=None,
            payload={"source_id": 9999},
        )


def test_schedule_creation_rejects_clip_source(db_session):
    source = Source(user_id=TEST_USER_ID, source_type="clip", name="Quick captures", status="active")
    db_session.add(source)
    db_session.commit()

    with pytest.raises(ValueError):
        create_schedule(
            db_session,
            TEST_USER_ID,
            job_type="ingest_source",
            name="Clip schedule",
            schedule_kind="daily",
            time_local="09:00",
            day_of_week=None,
            payload={"source_id": source.id},
        )


@pytest.fixture
def global_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)


@pytest.mark.skip(reason="Uses app.database.SessionLocal/engine (Postgres-bound); needs pgvector test infra.")
def test_due_jobs_run_once_and_advance_next_run(monkeypatch, global_db):
    monkeypatch.setattr(
        "app.services.schedules.ingest_source",
        lambda db, source_id: SimpleNamespace(status="success", documents_inserted=1, chunks_inserted=1),
    )
    with SessionLocal() as db:
        source = _seed_source(db)
        job = create_schedule(
            db,
            job_type="ingest_source",
            name="Overdue ingest",
            schedule_kind="daily",
            time_local="09:00",
            day_of_week=None,
            payload={"source_id": source.id},
        )
        job.next_run_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        db.commit()
        job_id = job.id

    first = run_due_jobs_once()
    second = run_due_jobs_once()

    assert first == 1
    assert second == 0

    with SessionLocal() as db:
        stored_job = db.get(ScheduledJob, job_id)
        assert stored_job is not None
        assert stored_job.next_run_at > datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.skip(reason="Uses app.database.SessionLocal/engine (Postgres-bound); needs pgvector test infra.")
def test_failed_due_job_stays_active_for_future_retries(monkeypatch, global_db):
    monkeypatch.setattr(
        "app.services.schedules.ingest_source",
        lambda db, source_id: SimpleNamespace(
            status="failed",
            documents_inserted=0,
            chunks_inserted=0,
            error_message="temporary network issue",
        ),
    )
    with SessionLocal() as db:
        source = _seed_source(db)
        job = create_schedule(
            db,
            job_type="ingest_source",
            name="Retrying ingest",
            schedule_kind="daily",
            time_local="09:00",
            day_of_week=None,
            payload={"source_id": source.id},
        )
        job.next_run_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        db.commit()
        job_id = job.id

    assert run_due_jobs_once() == 1

    with SessionLocal() as db:
        stored_job = db.get(ScheduledJob, job_id)
        assert stored_job is not None
        assert stored_job.status == "active"
        assert stored_job.last_error == "temporary network issue"
        assert stored_job.next_run_at > datetime.now(UTC).replace(tzinfo=None)
