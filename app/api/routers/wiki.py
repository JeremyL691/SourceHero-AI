"""Topics, pages, revisions, lint and export."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.core.errors import NotFoundError
from app.jobs import queue as q
from app.models.wiki import LintFinding, Topic, WikiPage
from app.schemas.wiki import (
    LintFindingOut,
    LintReport,
    PageDetailOut,
    PageOut,
    RevisionOut,
    TopicOut,
)
from app.wiki.export import export_zip, require_page
from app.wiki.links import backlinks
from app.wiki.lint import lint_wiki

router = APIRouter(tags=["wiki"])


def _sentence_count(page: WikiPage) -> int:
    body = page.body or {}
    return len(body.get("summary") or []) + sum(
        len(s.get("sentences") or []) for s in body.get("sections") or []
    )


def _lint_count(db: Session, page_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(LintFinding)
        .where(LintFinding.page_id == page_id, LintFinding.resolved_at.is_(None))
    ).scalar_one()


def _out(db: Session, page: WikiPage) -> PageOut:
    return PageOut.model_validate(page).model_copy(
        update={"sentence_count": _sentence_count(page), "lint_count": _lint_count(db, page.id)}
    )


@router.get("/topics", response_model=list[TopicOut], summary="List topics")
def get_topics(
    user: CurrentUser = Depends(require_scope("read")), db: Session = Depends(get_db)
) -> list[TopicOut]:
    rows = db.execute(select(Topic).where(Topic.user_id == user.id).order_by(Topic.slug)).scalars()
    return [TopicOut.model_validate(t) for t in rows]


@router.get("/wiki/pages", response_model=list[PageOut], summary="List pages")
def get_pages(
    page_status: str | None = None,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[PageOut]:
    stmt = select(WikiPage).where(WikiPage.user_id == user.id)
    if page_status:
        stmt = stmt.where(WikiPage.status == page_status)
    rows = db.execute(stmt.order_by(WikiPage.slug).limit(page.limit).offset(page.offset)).scalars()
    return [_out(db, p) for p in rows]


@router.get("/wiki/pages/{slug}", response_model=PageDetailOut, summary="One page")
def get_page(
    slug: str,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> PageDetailOut:
    from app.models.wiki import WikiPageClaim

    page = require_page(db, user.id, slug)
    claim_ids = list(
        db.execute(select(WikiPageClaim.claim_id).where(WikiPageClaim.page_id == page.id)).scalars()
    )
    return PageDetailOut.model_validate(page).model_copy(
        update={
            "claim_ids": claim_ids,
            "backlinks": [b.slug for b in backlinks(db, page)],
            "sentence_count": _sentence_count(page),
            "lint_count": _lint_count(db, page.id),
        }
    )


@router.get("/wiki/pages/{slug}/revisions", response_model=list[RevisionOut], summary="Revisions")
def get_revisions(
    slug: str,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[RevisionOut]:
    from app.models.wiki import WikiPageRevision

    page = require_page(db, user.id, slug)
    rows = db.execute(
        select(WikiPageRevision)
        .where(WikiPageRevision.page_id == page.id)
        .order_by(WikiPageRevision.revision.desc())
    ).scalars()
    return [RevisionOut.model_validate(r) for r in rows]


@router.get("/wiki/pages/{slug}/revisions/{revision}/diff", summary="Diff two revisions")
def get_revision_diff(
    slug: str,
    revision: int,
    to: int | None = None,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> dict:
    from app.wiki.compiler import diff_revisions

    page = require_page(db, user.id, slug)
    target = to if to is not None else revision + 1
    return {"from": revision, "to": target, "diff": diff_revisions(db, page, revision, target)}


@router.post("/wiki/pages/{slug}/compile", response_model=PageOut, summary="Force a recompile")
def post_compile(
    slug: str,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> PageOut:
    from app.wiki.compiler import compile_page
    from app.wiki.links import rebuild_links

    page = require_page(db, user.id, slug)
    compile_page(db, page, force=True)
    rebuild_links(db, page)
    db.commit()
    db.refresh(page)
    return _out(db, page)


@router.post("/wiki/plan", summary="Re-plan topics and queue dirty pages")
def post_plan(user: CurrentUser = Depends(require_scope("write")), db: Session = Depends(get_db)) -> dict:
    job = q.enqueue(
        db,
        "plan_topics",
        {"user_id": str(user.id)},
        user_id=user.id,
        dedupe_key=f"plan_topics:{user.id}",
    )
    db.commit()
    return {"job_id": job.id}


@router.get("/wiki/lint", response_model=LintReport, summary="Lint the wiki")
def get_lint(user: CurrentUser = Depends(require_scope("read")), db: Session = Depends(get_db)) -> LintReport:
    return LintReport(**lint_wiki(db, user.id))


@router.get("/wiki/lint/findings", response_model=list[LintFindingOut], summary="Open findings")
def get_findings(
    user: CurrentUser = Depends(require_scope("read")), db: Session = Depends(get_db)
) -> list[LintFindingOut]:
    rows = db.execute(
        select(LintFinding)
        .where(LintFinding.user_id == user.id, LintFinding.resolved_at.is_(None))
        .order_by(LintFinding.severity, LintFinding.id)
    ).scalars()
    return [LintFindingOut.model_validate(f) for f in rows]


@router.get("/wiki/export", summary="Export the wiki as a zip")
def get_export(user: CurrentUser = Depends(require_scope("read")), db: Session = Depends(get_db)) -> Response:
    payload = export_zip(db, user.id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sourcehero-wiki.zip"'},
    )


__all__ = ["NotFoundError", "router"]
