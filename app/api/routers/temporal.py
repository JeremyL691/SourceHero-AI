"""Belief diffs, stale notes, the timeline, and watches."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.core import time as clock
from app.core.errors import DomainValidationError
from app.jobs import queue as q
from app.schemas.temporal import (
    DiffCreateIn,
    DiffOut,
    StaleNoteOut,
    TimelineEntryOut,
    WatchCreateIn,
    WatchOut,
)
from app.temporal.belief_diff import compute_and_store_diff, compute_belief_diff
from app.temporal.staleness import detect_stale_notes
from app.temporal.timeline import timeline
from app.temporal.watches import create_watch, delete_watch, list_watches

router = APIRouter(tags=["temporal"])


def _out(row) -> DiffOut:
    """Shape a stored row, lifting the counts out of the payload.

    `stats` is not a column -- it is a section of the payload -- so validating the row
    directly would silently return an empty dict and the UI would show no counts.
    """
    scope_id_out = row.scope_id
    if row.scope_kind in ("cluster", "topic") and row.scope_id is not None:
        scope_id_out = row.scope_id.int
    return DiffOut.model_validate(row).model_copy(
        update={"scope_id": scope_id_out, "stats": (row.payload or {}).get("stats", {})}
    )


@router.get("/timeline", response_model=list[TimelineEntryOut], summary="Chronological claim events")
def get_timeline(
    cluster_id: int | None = None,
    topic_id: int | None = None,
    document_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    axis: str = Query("world", pattern="^(world|observed)$"),
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[TimelineEntryOut]:
    from app.core.errors import NotFoundError
    from app.models.claim import ClaimCluster
    from app.models.document import Document
    from app.models.wiki import Topic

    if cluster_id is not None:
        cluster = db.get(ClaimCluster, cluster_id)
        if cluster is None or cluster.user_id != user.id:
            raise NotFoundError("Cluster not found", code="cluster_not_found")
    if topic_id is not None:
        topic = db.get(Topic, topic_id)
        if topic is None or topic.user_id != user.id:
            raise NotFoundError("Topic not found", code="topic_not_found")
    if document_id is not None:
        doc = db.get(Document, document_id)
        if doc is None or doc.user_id != user.id:
            raise NotFoundError("Document not found", code="document_not_found")

    entries = timeline(
        db,
        user.id,
        cluster_id=cluster_id,
        topic_id=topic_id,
        document_id=document_id,
        start=start,
        end=end,
        axis=axis,
        limit=page.limit,
        offset=page.offset,
    )
    return [TimelineEntryOut(**e.as_dict()) for e in entries]


@router.post("/diffs", response_model=DiffOut, summary="Compute a belief diff")
def post_diff(
    payload: DiffCreateIn,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> DiffOut:
    if payload.end <= payload.start:
        raise DomainValidationError("end must be after start", code="bad_period")

    from app.core.errors import NotFoundError
    from app.models.claim import ClaimCluster
    from app.models.temporal import Watch
    from app.models.wiki import Topic

    if payload.scope_kind == "cluster" and payload.scope_id is not None:
        cid = payload.scope_id if isinstance(payload.scope_id, int) else payload.scope_id.int
        cluster = db.get(ClaimCluster, cid)
        if cluster is None or cluster.user_id != user.id:
            raise NotFoundError("Cluster not found", code="cluster_not_found")
    elif payload.scope_kind == "topic" and payload.scope_id is not None:
        tid = payload.scope_id if isinstance(payload.scope_id, int) else payload.scope_id.int
        topic = db.get(Topic, tid)
        if topic is None or topic.user_id != user.id:
            raise NotFoundError("Topic not found", code="topic_not_found")
    elif payload.scope_kind == "watch" and payload.scope_id is not None:
        wid = (
            payload.scope_id if isinstance(payload.scope_id, uuid.UUID) else uuid.UUID(str(payload.scope_id))
        )
        watch = db.get(Watch, wid)
        if watch is None or watch.user_id != user.id:
            raise NotFoundError("Watch not found", code="watch_not_found")

    from app.core.config import get_settings

    if get_settings().jobs_inline:
        row = compute_and_store_diff(
            db,
            user.id,
            start=payload.start,
            end=payload.end,
            scope_kind=payload.scope_kind,
            scope_id=payload.scope_id,
            time_axis=payload.time_axis,
        )
        return _out(row)

    job = q.enqueue(
        db,
        "belief_diff",
        {
            "start": payload.start.isoformat(),
            "end": payload.end.isoformat(),
            "scope_kind": payload.scope_kind,
            "scope_id": str(payload.scope_id) if payload.scope_id is not None else None,
            "time_axis": payload.time_axis,
        },
        user_id=user.id,
        now=clock.utcnow(),
    )
    db.commit()
    # No fabricated id: the diff does not exist yet, so an id here would match
    # nothing. The job id is the handle; the stored diff appears in GET /v1/diffs.
    return DiffOut(
        id=uuid.UUID(int=0),
        scope_kind=payload.scope_kind,
        scope_id=payload.scope_id,
        period_start=payload.start,
        period_end=payload.end,
        digest_method="queued",
        job_id=job.id,
    )


@router.get("/diffs", response_model=list[DiffOut], summary="List belief diffs")
def get_diffs(
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[DiffOut]:
    from app.models.temporal import BeliefDiff

    rows = (
        db.execute(
            BeliefDiff.__table__.select()
            .where(BeliefDiff.__table__.c.user_id == user.id)
            .order_by(BeliefDiff.__table__.c.period_start.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .mappings()
        .all()
    )
    return [DiffOut(**{**dict(row), "stats": (row["payload"] or {}).get("stats", {})}) for row in rows]


@router.get("/diffs/{diff_id}", response_model=DiffOut, summary="One belief diff")
def get_diff(
    diff_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> DiffOut:
    from app.core.errors import NotFoundError
    from app.models.temporal import BeliefDiff

    row = db.get(BeliefDiff, diff_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("diff not found")
    return _out(row)


@router.get("/stale-notes", response_model=list[StaleNoteOut], summary="Outdated notes")
def get_stale_notes(
    since: datetime | None = None,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[StaleNoteOut]:
    return [
        StaleNoteOut(**n.as_dict())
        for n in detect_stale_notes(db, user.id, since=since, limit=page.limit, offset=page.offset)
    ]


@router.get("/watches", response_model=list[WatchOut], summary="List watches")
def get_watches(
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[WatchOut]:
    return [
        WatchOut.model_validate(w) for w in list_watches(db, user.id, limit=page.limit, offset=page.offset)
    ]


@router.post("/watches", response_model=WatchOut, summary="Create a watch")
def post_watch(
    payload: WatchCreateIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> WatchOut:
    watch = create_watch(
        db,
        user.id,
        kind=payload.kind,
        source_id=payload.source_id,
        query=payload.query,
        refresh_interval_minutes=payload.refresh_interval_minutes,
    )
    return WatchOut.model_validate(watch)


@router.delete("/watches/{watch_id}", status_code=204, summary="Delete a watch")
def remove_watch(
    watch_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    delete_watch(db, user.id, watch_id)


__all__ = ["compute_belief_diff", "router"]
