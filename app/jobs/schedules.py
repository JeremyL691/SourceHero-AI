"""Recurring triggers: CRUD plus the pure `compute_next_run_at` the scheduler uses.

Execution always goes through the queue. Nothing here runs a handler in-process.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainValidationError, NotFoundError
from app.core.time import utcnow
from app.models.job import Job, ScheduledJob

JOB_TYPES = ("refresh_source", "belief_diff", "plan_topics")


def compute_next_run_at(job: ScheduledJob, *, after: datetime | None = None) -> datetime | None:
    """Next occurrence strictly after `after`.

    Advances from the *scheduled* time rather than from now, in whole periods, so a
    job that fell several slots behind while the scheduler was down skips straight
    to the next future slot instead of replaying (or stalling on) a past one.
    """
    # `enabled` is None on a not-yet-flushed instance (the column default applies at
    # INSERT), so only an explicit False disables a schedule.
    if job.enabled is False:
        return None
    now = after or utcnow()
    base = job.next_run_at or now
    # Column defaults apply at INSERT, so both may still be None on a fresh instance.
    target_hour = job.cron_hour if job.cron_hour is not None else 0
    target_minute = job.cron_minute if job.cron_minute is not None else 0
    weekly = job.cron_day_of_week is not None
    step = timedelta(days=7 if weekly else 1)

    candidate = base.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if weekly:
        # 0 = Monday, matching Python's weekday(). Line the candidate up on the
        # requested weekday before stepping forward.
        candidate += timedelta(days=(job.cron_day_of_week - candidate.weekday()) % 7)
    # Step whole periods until strictly in the future. One `+=` is not enough: a
    # schedule that missed several slots would otherwise land on a past date.
    while candidate <= now:
        candidate += step
    return candidate


def create_schedule(
    db: Session,
    user_id: uuid.UUID,
    *,
    job_type: str,
    config: dict | None = None,
    name: str = "",
    day_of_week: int | None = None,
    hour: int = 0,
    minute: int = 0,
) -> ScheduledJob:
    if job_type not in JOB_TYPES:
        raise DomainValidationError(f"job_type must be one of {', '.join(JOB_TYPES)}", code="bad_job_type")
    if day_of_week is not None and not 0 <= day_of_week <= 6:
        raise DomainValidationError("day_of_week must be 0 (Monday) to 6", code="bad_day")
    job = ScheduledJob(
        user_id=user_id,
        name=name or job_type.replace("_", " "),
        job_type=job_type,
        config=config or {},
        cron_day_of_week=day_of_week,
        cron_hour=hour,
        cron_minute=minute,
    )
    job.next_run_at = compute_next_run_at(job)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_schedules(db: Session, user_id: uuid.UUID) -> list[ScheduledJob]:
    return list(
        db.execute(
            select(ScheduledJob).where(ScheduledJob.user_id == user_id).order_by(ScheduledJob.id)
        ).scalars()
    )


def get_schedule(db: Session, user_id: uuid.UUID, schedule_id: int) -> ScheduledJob:
    job = db.get(ScheduledJob, schedule_id)
    if job is None or job.user_id != user_id:
        raise NotFoundError("schedule not found")
    return job


def update_schedule(db: Session, user_id: uuid.UUID, schedule_id: int, **changes) -> ScheduledJob:
    job = get_schedule(db, user_id, schedule_id)
    for key in ("name", "config", "enabled", "cron_day_of_week", "cron_hour", "cron_minute"):
        if key in changes and changes[key] is not None:
            setattr(job, key, changes[key])
    job.next_run_at = compute_next_run_at(job)
    db.commit()
    db.refresh(job)
    return job


def delete_schedule(db: Session, user_id: uuid.UUID, schedule_id: int) -> None:
    job = get_schedule(db, user_id, schedule_id)
    db.delete(job)
    db.commit()


def list_runs(db: Session, user_id: uuid.UUID, schedule_id: int) -> list[Job]:
    """Queue jobs produced by this schedule's ticks, newest first.

    The scheduler enqueues one job per fired slot with dedupe_key `sched:<id>:<slot>`
    and never writes ScheduledJobRun rows, so the queue is the run history.
    """
    schedule = get_schedule(db, user_id, schedule_id)
    return list(
        db.execute(
            select(Job)
            .where(Job.user_id == user_id, Job.dedupe_key.like(f"sched:{schedule.id}:%"))
            .order_by(Job.id.desc())
        ).scalars()
    )


__all__ = [
    "JOB_TYPES",
    "compute_next_run_at",
    "create_schedule",
    "delete_schedule",
    "get_schedule",
    "list_runs",
    "list_schedules",
    "update_schedule",
]
