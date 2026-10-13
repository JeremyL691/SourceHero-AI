"""Evidence brief exports: one answer or one topic as a Markdown file."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_scope
from app.briefs import build_answer_brief, build_topic_brief

router = APIRouter(tags=["briefs"])


def _markdown_response(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/answers/{answer_id}/export", summary="Export an answer as an evidence brief")
def export_answer(
    answer_id: uuid.UUID,
    format: Literal["md"] = Query("md"),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> Response:
    body = build_answer_brief(db, user.id, answer_id)
    return _markdown_response(body, f"answer-{answer_id}.md")


@router.get("/topics/{topic_id}/brief", summary="Export a topic as an evidence brief")
def export_topic(
    topic_id: int,
    format: Literal["md"] = Query("md"),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> Response:
    body = build_topic_brief(db, user.id, topic_id)
    return _markdown_response(body, f"topic-{topic_id}.md")


__all__ = ["router"]
