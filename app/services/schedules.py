from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ScheduledJob, ScheduledJobRun, Source
from app.services.briefing import generate_briefing
from app.services.pipeline import INGESTABLE_SOURCE_TYPES, SourcePausedError, ingest_source

logger = logging.getLogger(__name__)

VALID_JOB_TYPES = {"ingest_source", "generate_briefing"}
VALID_JOB_STATUSES = {"active", "paused", "failed"}
VALID_SCHEDULE_KINDS = {"daily", "weekly"}


def create_schedule(
    db: Session,
    user_id: str,
    *,
    job_type: str,
    name: str,
    schedule_kind: str,
    time_local: str,
    day_of_week: int | None,
    payload: dict,
) -> ScheduledJob:
    _validate_schedule(job_type=job_type, schedule_kind=schedule_kind, time_local=time_local, day_of_week=day_of_week)
    _validate_payload(db, user_id, job_type, payload)
    job = ScheduledJob(
        user_id=user_id,
        job_type=job_type,
        name=name.strip() or _default_job_name(job_type, payload),
        status="active",
        schedule_kind=schedule_kind,
        time_local=time_local,
        day_of_week=day_of_week,
        payload_json=json.dumps(payload, ensure_ascii=False),
        next_run_at=compute_next_run_at(schedule_kind, time_local, day_of_week),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_schedule(
    db: Session,
    user_id: str,
    schedule_id: int,
    *,
    name: str | None = None,
    status: str | None = None,
    schedule_kind: str | None = None,
    time_local: str | None = None,
    day_of_week: int | None = None,
    payload: dict | None = None,
) -> ScheduledJob:
    job = _get_job(db, user_id, schedule_id)
    new_kind = schedule_kind or job.schedule_kind
    new_time = time_local or job.time_local
    if new_kind == "weekly":
        new_day = job.day_of_week if day_of_week is None else day_of_week
    else:
        new_day = None
    _validate_schedule(
        job_type=job.job_type,
        schedule_kind=new_kind,
        time_local=new_time,
        day_of_week=new_day,
        status=status or job.status,
    )
    if payload is not None:
        _validate_payload(db, user_id, job.job_type, payload)
    if name is not None:
        job.name = name.strip() or job.name
    if status is not None:
        job.status = status
    if schedule_kind is not None:
        job.schedule_kind = schedule_kind
    if time_local is not None:
        job.time_local = time_local
    job.day_of_week = new_day
    if payload is not None:
        job.payload_json = json.dumps(payload, ensure_ascii=False)
    if (
        schedule_kind is not None
        or time_local is not None
        or (job.schedule_kind == "weekly" and day_of_week is not None)
        or status == "active"
    ):
        job.next_run_at = compute_next_run_at(job.schedule_kind, job.time_local, job.day_of_week)
    db.commit()
    db.refresh(job)
    return job


def delete_schedule(db: Session, user_id: str, schedule_id: int) -> None:
    job = _get_job(db, user_id, schedule_id)
    db.delete(job)
    db.commit()


def run_schedule_now(db: Session, user_id: str, schedule_id: int) -> ScheduledJobRun:
    job = _get_job(db, user_id, schedule_id)
    return _execute_job(db, user_id, job)


def run_due_jobs_once() -> int:
    executed = 0
    with SessionLocal() as db:
        due_jobs = db.scalars(
            select(ScheduledJob)
            .where(ScheduledJob.status == "active", ScheduledJob.next_run_at <= _utc_now())
            .order_by(ScheduledJob.next_run_at, ScheduledJob.id)
        ).all()
        for job in due_jobs:
            _execute_job(db, job.user_id, job)
            executed += 1
    return executed


async def scheduler_poller() -> None:
    interval = int(os.getenv("SOURCEHERO_SCHEDULER_POLL_SECONDS", "60"))
    while True:
        try:
            run_due_jobs_once()
        except Exception:
            logger.exception("Scheduled job poller failed")
        await asyncio.sleep(interval)


def schedule_payload(job: ScheduledJob) -> dict:
    try:
        payload = json.loads(job.payload_json or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def compute_next_run_at(schedule_kind: str, time_local: str, day_of_week: int | None, now: datetime | None = None) -> datetime:
    local_now = (now or datetime.now().astimezone()).astimezone()
    local_tz = local_now.tzinfo
    hour, minute = _parse_time_local(time_local)
    if schedule_kind == "daily":
        candidate_date = local_now.date()
        candidate = datetime.combine(candidate_date, time(hour=hour, minute=minute), tzinfo=local_tz)
        if candidate <= local_now:
            candidate += timedelta(days=1)
    else:
        if day_of_week is None:
            raise ValueError("Weekly schedules require day_of_week")
        days_ahead = (day_of_week - local_now.weekday()) % 7
        candidate_date = local_now.date() + timedelta(days=days_ahead)
        candidate = datetime.combine(candidate_date, time(hour=hour, minute=minute), tzinfo=local_tz)
        if candidate <= local_now:
            candidate += timedelta(days=7)
    return candidate.astimezone(UTC).replace(tzinfo=None)


def _execute_job(db: Session, user_id: str, job: ScheduledJob) -> ScheduledJobRun:
    payload = schedule_payload(job)
    run = ScheduledJobRun(job_id=job.id, user_id=user_id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        if job.job_type == "ingest_source":
            source_id = int(payload["source_id"])
            ingest_run = ingest_source(db, user_id, source_id)
            if ingest_run.status != "success":
                raise RuntimeError(ingest_run.error_message or f"Ingestion finished with status `{ingest_run.status}`.")
            run.summary = (
                f"status={ingest_run.status}, docs={ingest_run.documents_inserted}, chunks={ingest_run.chunks_inserted}"
            )
            run.status = ingest_run.status
        elif job.job_type == "generate_briefing":
            briefing = generate_briefing(
                db,
                user_id=user_id,
                topic=str(payload["topic"]),
                top_k=int(payload.get("top_k", 8)),
                source_ids=payload.get("source_ids"),
                source_type=payload.get("source_type"),
                collection_id=payload.get("collection_id"),
                tags=payload.get("tags"),
            )
            run.summary = f"briefing_id={briefing.id}"
            run.status = "success"
        else:
            raise ValueError(f"Unsupported job type: {job.job_type}")
        run.ended_at = _utc_now()
        job.last_run_at = run.ended_at
        job.next_run_at = compute_next_run_at(job.schedule_kind, job.time_local, job.day_of_week)
        job.last_error = None
        job.status = "active"
        db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(ScheduledJobRun, run.id)
        job = db.get(ScheduledJob, job.id)
        if run:
            run.status = "failed"
            run.ended_at = _utc_now()
            run.error_message = _friendly_schedule_error(exc)
        if job:
            job.last_run_at = run.ended_at if run else _utc_now()
            job.next_run_at = compute_next_run_at(job.schedule_kind, job.time_local, job.day_of_week)
            job.last_error = _friendly_schedule_error(exc)
            job.status = "active"
        db.commit()
    db.refresh(run)
    return run


def _friendly_schedule_error(exc: Exception) -> str:
    if isinstance(exc, SourcePausedError):
        return str(exc)
    return str(exc).splitlines()[0][:200] or exc.__class__.__name__


def _get_job(db: Session, user_id: str, schedule_id: int) -> ScheduledJob:
    job = db.get(ScheduledJob, schedule_id)
    if not job:
        raise ValueError(f"Schedule not found: {schedule_id}")
    if job.user_id != user_id:
        raise ValueError(f"Schedule not found: {schedule_id}")
    return job


def _validate_schedule(
    *,
    job_type: str,
    schedule_kind: str,
    time_local: str,
    day_of_week: int | None,
    status: str = "active",
) -> None:
    if job_type not in VALID_JOB_TYPES:
        raise ValueError("job_type must be ingest_source or generate_briefing")
    if schedule_kind not in VALID_SCHEDULE_KINDS:
        raise ValueError("schedule_kind must be daily or weekly")
    if status not in VALID_JOB_STATUSES:
        raise ValueError("status must be active, paused, or failed")
    _parse_time_local(time_local)
    if schedule_kind == "weekly" and (day_of_week is None or day_of_week not in range(7)):
        raise ValueError("Weekly schedules require day_of_week between 0 and 6")


def _validate_payload(db: Session, user_id: str, job_type: str, payload: dict) -> None:
    if job_type == "ingest_source":
        source_id = payload.get("source_id")
        if not isinstance(source_id, int):
            raise ValueError("ingest_source schedules require integer payload.source_id")
        source = db.get(Source, source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        if source.user_id != user_id:
            raise ValueError(f"Source not found: {source_id}")
        if source.source_type not in INGESTABLE_SOURCE_TYPES:
            raise ValueError("Only rss, webpage, and pdf sources can be scheduled for ingestion.")
        return
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        raise ValueError("generate_briefing schedules require payload.topic")


def _parse_time_local(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except Exception as exc:
        raise ValueError("time_local must be HH:MM in 24-hour format") from exc
    if hour not in range(24) or minute not in range(60):
        raise ValueError("time_local must be HH:MM in 24-hour format")
    return hour, minute


def _default_job_name(job_type: str, payload: dict) -> str:
    if job_type == "ingest_source":
        return f"Auto ingest source {payload.get('source_id')}"
    return f"Auto briefing: {payload.get('topic', 'untitled')}"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
