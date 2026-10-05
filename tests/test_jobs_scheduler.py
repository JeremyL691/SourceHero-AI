"""M3.3/M3.5: the scheduler tick is advisory-locked and idempotent."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.jobs import queue as q
from app.jobs.scheduler import tick
from app.jobs.schedules import compute_next_run_at, create_schedule
from app.models.job import Job, ScheduledJob
from app.models.source import Source

pytestmark = pytest.mark.db

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _manual_queue(no_inline_jobs):
    pass


# --- compute_next_run_at -------------------------------------------------


def test_daily_schedule_advances_to_the_next_matching_slot(db):
    job = ScheduledJob(user_id=uuid.uuid4(), job_type="refresh_source", cron_hour=3, cron_minute=0)
    # Now is noon; 03:00 today has passed, so the next slot is tomorrow.
    assert compute_next_run_at(job, after=T0) == datetime(2026, 6, 2, 3, 0, tzinfo=UTC)


def test_weekly_schedule_advances_seven_days_when_the_day_already_passed(db):
    # 2026-06-01 is a Monday, so cron_day_of_week=0 is today.
    job = ScheduledJob(
        user_id=uuid.uuid4(), job_type="belief_diff", cron_day_of_week=0, cron_hour=8, cron_minute=0
    )
    assert compute_next_run_at(job, after=T0) == datetime(2026, 6, 8, 8, 0, tzinfo=UTC)


def test_weekly_schedule_can_land_later_this_week(db):
    job = ScheduledJob(
        user_id=uuid.uuid4(), job_type="belief_diff", cron_day_of_week=3, cron_hour=8, cron_minute=0
    )
    # Monday + 3 days = Thursday.
    assert compute_next_run_at(job, after=T0) == datetime(2026, 6, 4, 8, 0, tzinfo=UTC)


def test_disabled_schedule_has_no_next_run(db):
    job = ScheduledJob(user_id=uuid.uuid4(), job_type="refresh_source", enabled=False)
    assert compute_next_run_at(job, after=T0) is None


def test_a_late_tick_still_lands_on_a_weekday_slot(db):
    """A scheduler that was briefly down must not drag a weekly job off its cadence.

    The job's slot (Monday 08:00) fell on 2026-05-25; the tick happens on Wednesday
    2026-06-10. The next run must be the next Monday (06-15), never a mid-week day
    and never a Monday already in the past.
    """
    job = ScheduledJob(
        user_id=uuid.uuid4(),
        job_type="belief_diff",
        cron_day_of_week=0,
        cron_hour=8,
        next_run_at=datetime(2026, 5, 25, 8, 0, tzinfo=UTC),
    )
    late = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)  # a Wednesday
    result = compute_next_run_at(job, after=late)
    assert result == datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    assert result.weekday() == 0, "must stay on Monday"
    assert result > late


# --- the tick ------------------------------------------------------------


def test_tick_enqueues_due_schedules_and_advances_them(fresh_db):
    db = fresh_db
    uid = uuid.uuid4()
    job = create_schedule(
        db, uid, job_type="refresh_source", config={"source_id": "abc"}, day_of_week=0, hour=3
    )
    # create_schedule schedules from the real clock; force it into the past so the
    # fixture is due at the frozen T0.
    job.next_run_at = T0 - timedelta(hours=1)
    db.commit()

    counts = tick(db, now=T0)
    assert counts["scheduled_jobs"] == 1
    queued = db.execute(select(Job)).scalars().all()
    assert len(queued) == 1
    assert queued[0].kind == "refresh_source"
    assert queued[0].user_id == uid
    assert queued[0].payload == {"source_id": "abc"}
    assert queued[0].dedupe_key.startswith(f"sched:{job.id}:")


def test_tick_is_idempotent(fresh_db):
    """Two ticks in a row must not enqueue the same schedule twice."""
    db = fresh_db
    job = create_schedule(db, uuid.uuid4(), job_type="refresh_source", day_of_week=0, hour=3)
    job.next_run_at = T0 - timedelta(hours=1)
    db.commit()

    first = tick(db, now=T0)
    second = tick(db, now=T0)
    assert first["scheduled_jobs"] == 1
    assert second["scheduled_jobs"] == 0, "the schedule already advanced past its slot"
    assert q.pending(db, now=T0, drain=True) == 1


def test_tick_respects_the_advisory_lock(fresh_db, engine):
    """A second connection holding the lock must make the tick a no-op."""
    from sqlalchemy.orm import Session

    db = fresh_db
    job = create_schedule(db, uuid.uuid4(), job_type="refresh_source", day_of_week=0, hour=3)
    job.next_run_at = T0 - timedelta(hours=1)
    db.commit()

    other = Session(bind=engine)
    try:
        held = other.begin()
        other.execute(text("SELECT pg_advisory_xact_lock(hashtext('sourcehero-scheduler'))"))
        counts = tick(db, now=T0)
        assert counts["skipped"] == "another worker is scheduling"
        assert q.pending(db, now=T0, drain=True) == 0
        held.rollback()
    finally:
        other.close()


def test_tick_enqueues_watched_sources(fresh_db):
    db = fresh_db
    uid = uuid.uuid4()
    source = Source(
        user_id=uid,
        source_type="webpage",
        name="Watched",
        url="https://example.test/a",
        watch=True,
        status="active",
        next_refresh_at=T0 - timedelta(hours=1),
        refresh_interval_minutes=60,
    )
    db.add(source)
    db.commit()

    counts = tick(db, now=T0)
    assert counts["watch_sources"] == 1
    db.refresh(source)
    assert source.next_refresh_at == T0 + timedelta(minutes=60)

    jobs = db.execute(select(Job)).scalars().all()
    assert [j.kind for j in jobs] == ["refresh_source"]


def test_tick_ignores_paused_sources(fresh_db):
    db = fresh_db
    db.add(
        Source(
            user_id=uuid.uuid4(),
            source_type="webpage",
            name="Paused",
            watch=True,
            status="paused",
            next_refresh_at=T0 - timedelta(hours=1),
        )
    )
    db.commit()
    assert tick(db, now=T0)["watch_sources"] == 0


def test_maintenance_survives_tables_that_do_not_exist_yet(fresh_db):
    """The llm_cache table arrives with M6; before then the tick must not die on it."""
    db = fresh_db
    assert tick(db, now=T0)["scheduled_jobs"] == 0


def test_schedule_crud(db):
    from app.core.errors import NotFoundError
    from app.jobs.schedules import (
        delete_schedule,
        get_schedule,
        list_schedules,
        update_schedule,
    )

    uid = uuid.uuid4()
    job = create_schedule(db, uid, job_type="plan_topics", name="nightly")
    assert [s.id for s in list_schedules(db, uid)] == [job.id]

    updated = update_schedule(db, uid, job.id, cron_hour=5, enabled=False)
    assert updated.cron_hour == 5 and updated.next_run_at is None

    delete_schedule(db, uid, job.id)
    assert list_schedules(db, uid) == []
    with pytest.raises(NotFoundError):
        get_schedule(db, uid, job.id)


def test_schedule_of_another_user_is_not_found(db):
    from app.core.errors import NotFoundError
    from app.jobs.schedules import get_schedule

    mine = create_schedule(db, uuid.uuid4(), job_type="plan_topics")
    with pytest.raises(NotFoundError):
        get_schedule(db, uuid.uuid4(), mine.id)


def test_invalid_job_type_is_rejected(db):
    from app.core.errors import DomainValidationError

    with pytest.raises(DomainValidationError):
        create_schedule(db, uuid.uuid4(), job_type="rm -rf /")


def test_watch_creation_sets_next_refresh_at(fresh_db):
    """DATA-8 regression test:

    Creating a watch or marking a source watched must set next_refresh_at.
    """
    from app.ingestion.sources import create_source, update_source
    from app.temporal.watches import create_watch

    uid = uuid.uuid4()
    # 1. create_source with watch=True
    src1 = create_source(
        fresh_db, uid, source_type="webpage", name="W1", url="https://example.com/1", watch=True
    )
    assert src1.next_refresh_at is not None

    # 2. update_source with watch=True
    src2 = create_source(
        fresh_db, uid, source_type="webpage", name="W2", url="https://example.com/2", watch=False
    )
    assert src2.next_refresh_at is None
    src2_up = update_source(fresh_db, uid, src2.id, watch=True)
    assert src2_up.next_refresh_at is not None

    # 3. create_watch for source
    src3 = create_source(
        fresh_db, uid, source_type="webpage", name="W3", url="https://example.com/3", watch=False
    )
    assert src3.next_refresh_at is None
    create_watch(fresh_db, uid, kind="source", source_id=src3.id)
    fresh_db.refresh(src3)
    assert src3.watch is True
    assert src3.next_refresh_at is not None


def test_weekly_diff_scheduler_tick(fresh_db):
    """DATA-8 regression test:

    Weekly diff tick enqueues a belief diff once per week per user with watches.
    """
    from app.ingestion.sources import create_source
    from app.temporal.watches import create_watch

    db = fresh_db
    uid = uuid.uuid4()
    src = create_source(db, uid, source_type="webpage", name="W", url="https://example.com/w")
    create_watch(db, uid, kind="source", source_id=src.id)

    # Week 1
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    c1 = tick(db, now=t1)
    assert c1["weekly_diff"] == 1

    # Same week later
    c1_again = tick(db, now=t1 + timedelta(days=2))
    assert c1_again["weekly_diff"] == 0

    # Week 2 (7 days later)
    t2 = t1 + timedelta(days=7)
    c2 = tick(db, now=t2)
    assert c2["weekly_diff"] == 1


def test_query_watch_tick_evaluation(fresh_db):
    """DATA-8 regression test:

    Query watch tick evaluates new claims since last_checked_at and records hits.
    """
    from app.indexing.embeddings import get_embedder
    from app.ingestion.sources import create_source
    from app.models.claim import Claim
    from app.models.temporal import BeliefDiff
    from app.temporal.watches import create_watch

    db = fresh_db
    uid = uuid.uuid4()
    embedder = get_embedder()

    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    # Create query watch
    q_watch = create_watch(db, uid, kind="query", query="battery energy density")
    q_watch.created_at = t0
    db.commit()

    # First tick: no new claims
    c0 = tick(db, now=t0)
    assert c0["query_watches"] == 0

    # Add a matching claim asserted at t0 + 1 min
    src = create_source(db, uid, source_type="webpage", name="S", url="https://example.com/s")
    from app.ingestion.types import ExtractedDocument
    from app.ingestion.versions import upsert_document_version

    res = upsert_document_version(
        db,
        source=src,
        extracted=ExtractedDocument(
            title="Doc", text="The battery has very high energy density.", url=src.url
        ),
    )
    claim = Claim(
        user_id=uid,
        document_id=res.document.id,
        version_id=res.version.id,
        statement="The battery has very high energy density.",
        quote="high energy density",
        char_start=0,
        char_end=18,
        embedding=embedder.embed_query("The battery has very high energy density."),
        asserted_at=t0 + timedelta(minutes=1),
        observed_at=t0 + timedelta(minutes=1),
        created_at=t0 + timedelta(minutes=1),
        status="active",
        extractor="heuristic",
        claim_hash="hash_qw",
    )
    db.add(claim)
    db.commit()

    # Second tick: should hit query watch
    t1 = t0 + timedelta(minutes=5)
    c1 = tick(db, now=t1)
    assert c1["query_watches"] == 1

    db.refresh(q_watch)
    assert q_watch.last_checked_at == t1
    diffs = list(db.execute(select(BeliefDiff).where(BeliefDiff.user_id == uid)).scalars())
    assert len(diffs) >= 1
