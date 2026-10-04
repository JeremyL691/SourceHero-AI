"""Job inspection and control. A user sees only their own jobs; `user_id IS NULL`
marks a system job, which only an admin can see."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    get_current_user,
    get_db,
    require_admin_write,
    require_scope,
)
from app.core.errors import NotFoundError
from app.jobs import queue as q
from app.models.job import Job
from app.schemas.jobs import JobActionOut, JobOut

router = APIRouter(tags=["jobs"])


def _visible(user: CurrentUser):
    """A user sees their own jobs; an admin additionally sees system jobs (user_id NULL).

    Returning `None` for admins would filter on `user_id IS NULL` and hide their own
    work, which is not what "admins can see system jobs too" means.
    """
    if user.is_admin:
        return or_(Job.user_id == user.id, Job.user_id.is_(None))
    return Job.user_id == user.id


def _load(db: Session, user: CurrentUser, job_id: int) -> Job:
    """Fetch a job the caller is allowed to see.

    Same rule as `_visible`: your own jobs, plus system jobs if you are an admin.
    An admin does *not* get to read another user's jobs -- tenancy holds for
    administrators too. 404 rather than 403, so the response never confirms an id.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError("job not found")
    if job.user_id == user.id:
        return job
    if user.is_admin and job.user_id is None:
        return job
    raise NotFoundError("job not found")


@router.get("/jobs", summary="List jobs")
def list_jobs(
    job_status: str | None = Query(
        None, alias="status", description="queued|running|succeeded|failed|dead|cancelled"
    ),
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobOut]:
    stmt = select(Job).where(_visible(user))
    if job_status:
        stmt = stmt.where(Job.status == job_status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    stmt = stmt.order_by(Job.id.desc()).limit(limit).offset(offset)
    return [JobOut.model_validate(j) for j in db.execute(stmt).scalars()]


@router.get("/jobs/{job_id}", summary="Get one job")
def get_job(
    job_id: int,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    return JobOut.model_validate(_load(db, user, job_id))


@router.post(
    "/jobs/{job_id}/retry",
    summary="Requeue a failed or dead job",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobActionOut,
)
def retry_job(
    job_id: int,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> JobActionOut:
    job = _load(db, user, job_id)
    if not q.retry(db, job.id):
        raise NotFoundError("job is not in a retryable state", code="not_retryable")
    db.commit()
    return JobActionOut(job_id=job.id, status="queued")


@router.post(
    "/jobs/{job_id}/cancel",
    summary="Cancel a queued job, or ask a running one to stop",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobActionOut,
)
def cancel_job(
    job_id: int,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> JobActionOut:
    job = _load(db, user, job_id)
    immediate = q.cancel(db, job.id)
    db.commit()
    return JobActionOut(job_id=job.id, cancelled=immediate, status="cancelled" if immediate else "running")


@router.post("/index/reindex", summary="Requeue recomputation of embeddings", status_code=202)
def reindex_embeddings(
    user: CurrentUser = Depends(require_admin_write),
    db: Session = Depends(get_db),
) -> dict:
    """Queues `reindex_embeddings`; the handler lands with M5's indexer."""
    job = q.enqueue(db, "reindex_embeddings", {}, user_id=user.id, dedupe_key="reindex_embeddings")
    db.commit()
    return {"job_id": job.id}


__all__ = ["router"]
