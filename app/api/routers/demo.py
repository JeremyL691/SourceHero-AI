"""Demo seeding and the answer history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.core import time as clock
from app.core.errors import NotFoundError
from app.jobs import queue as q
from app.models.answer import Answer
from app.schemas.verify import AnswerOut, SaveNoteRequest
from app.verify.verifier import verify_text

router = APIRouter(tags=["demo"])


@router.post(
    "/demo/seed",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue the demo corpus for this account",
)
def seed_demo(
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> dict:
    """Load the demo corpus and return immediately.

    Queued rather than run inline: seeding walks the whole ingestion pipeline, and a
    synchronous request would hold the connection open for the whole of it. The client
    polls the jobs list instead.
    """
    job = q.enqueue(
        db,
        "seed_demo_corpus",
        # A worker is already consuming this queue; draining here as well would run
        # the pipeline twice over the same corpus and deadlock.
        {"user_id": str(user.id), "drain": False},
        user_id=user.id,
        dedupe_key=f"seed_demo:{user.id}",
        now=clock.utcnow(),
    )
    db.commit()
    return {"job_id": job.id, "status": "queued"}


@router.get("/answers", response_model=list[AnswerOut], summary="Your past answers")
def list_answers(
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[AnswerOut]:
    rows = db.execute(
        select(Answer)
        .where(Answer.user_id == user.id)
        .order_by(Answer.created_at.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).scalars()
    return [AnswerOut.model_validate(r) for r in rows]


@router.get("/answers/{answer_id}", response_model=AnswerOut, summary="One stored answer")
def get_answer(
    answer_id: uuid.UUID,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> AnswerOut:
    row = db.get(Answer, answer_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("answer not found")
    return AnswerOut.model_validate(row)


@router.post(
    "/answers/{answer_id}/save-note",
    summary="Save an answer as a note in your library",
)
def save_note(
    answer_id: uuid.UUID,
    payload: SaveNoteRequest | None = None,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> dict:
    """Keep an answer as a first-class note, so it is itself later verifiable."""
    from app.ingestion.sources import create_source
    from app.ingestion.types import ExtractedDocument
    from app.ingestion.versions import upsert_document_version

    row = db.get(Answer, answer_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("answer not found")

    body = f"{row.question}\n\n{row.answer_md}".strip()
    title = (payload.title if payload else None) or f"Answer: {row.question[:60]}"
    source = create_source(db, user.id, source_type="note", name=title)
    db.refresh(source)

    res = upsert_document_version(
        db, source=source, extracted=ExtractedDocument(title=title, text=body), origin="upload"
    )
    db.commit()

    job = q.enqueue(
        db,
        "index_version",
        {"version_id": res.version.id} if res.version else {},
        user_id=user.id,
        dedupe_key=f"index-answer:{answer_id}",
        now=clock.utcnow(),
    )
    db.commit()
    return {"source_id": str(source.id), "job_id": job.id}


@router.post("/verify/quick", summary="Verify a sentence without persisting it")
def verify_quick(
    text: str,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> dict:
    """A convenience alias used by the Verify page's live preview."""
    return verify_text(db, user.id, text).as_dict()


__all__ = ["router"]
