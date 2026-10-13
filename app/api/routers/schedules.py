"""Recurring triggers: CRUD over schedules and their run history. A user sees only their own."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_scope
from app.core.errors import DomainValidationError, NotFoundError
from app.jobs import schedules as svc
from app.models.source import Source
from app.schemas.jobs import JobOut
from app.schemas.schedules import ScheduleIn, ScheduleOut, SchedulePatch

router = APIRouter(tags=["schedules"])


def _check_source(db: Session, user: CurrentUser, config: dict) -> None:
    """A `source_id` in the payload must name a source the caller owns (404 otherwise)."""
    raw = config.get("source_id")
    if raw is None:
        return
    try:
        source_id = uuid.UUID(str(raw))
    except ValueError as exc:
        raise DomainValidationError("source_id must be a UUID", code="bad_source_id") from exc
    source = db.get(Source, source_id)
    if source is None or source.user_id != user.id:
        raise NotFoundError("source not found")


@router.get("/schedules", response_model=list[ScheduleOut], summary="List schedules")
def list_schedules(
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[ScheduleOut]:
    return [ScheduleOut.model_validate(j) for j in svc.list_schedules(db, user.id)]


@router.post(
    "/schedules",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule",
)
def create_schedule(
    payload: ScheduleIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ScheduleOut:
    if payload.job_type not in svc.JOB_TYPES:
        raise DomainValidationError(
            f"job_type must be one of {', '.join(svc.JOB_TYPES)}", code="bad_job_type"
        )
    _check_source(db, user, payload.config)
    job = svc.create_schedule(
        db,
        user.id,
        job_type=payload.job_type,
        config=payload.config,
        name=payload.name,
        day_of_week=payload.day_of_week,
        hour=payload.hour,
        minute=payload.minute,
    )
    return ScheduleOut.model_validate(job)


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut, summary="Update a schedule")
def update_schedule(
    schedule_id: int,
    payload: SchedulePatch,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ScheduleOut:
    if payload.config is not None:
        _check_source(db, user, payload.config)
    job = svc.update_schedule(
        db,
        user.id,
        schedule_id,
        name=payload.name,
        config=payload.config,
        enabled=payload.enabled,
        cron_day_of_week=payload.day_of_week,
        cron_hour=payload.hour,
        cron_minute=payload.minute,
    )
    return ScheduleOut.model_validate(job)


@router.delete(
    "/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a schedule",
)
def delete_schedule(
    schedule_id: int,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> Response:
    svc.delete_schedule(db, user.id, schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/schedules/{schedule_id}/runs",
    response_model=list[JobOut],
    summary="Queue jobs fired by this schedule, newest first",
)
def list_runs(
    schedule_id: int,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    return [JobOut.model_validate(j) for j in svc.list_runs(db, user.id, schedule_id)]


__all__ = ["router"]
