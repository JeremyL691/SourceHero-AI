"""Wiki handlers: plan topics, compile pages, lint."""

from __future__ import annotations

from sqlalchemy import select

from app.jobs.registry import JobContext, job_handler


@job_handler("plan_topics")
def plan_topics_job(ctx: JobContext, payload: dict) -> dict:
    """Group clusters into topics, then queue every page whose inputs moved."""
    import uuid

    from app.services.advisory import session_lock
    from app.wiki.topics import plan_topics

    raw = payload.get("user_id")
    if not raw:
        return {"skipped": "no user_id in payload"}
    user_id = uuid.UUID(str(raw))

    # Planning reads claims other handlers have written but not yet committed;
    # without the lock two workers can plan the same corpus concurrently.
    with session_lock(ctx.db, f"claims:{user_id}") as acquired:
        if not acquired:
            return {"skipped": "another worker is planning"}
        result = plan_topics(ctx.db, user_id)

    from app.models.wiki import WikiPage

    dirty = (
        ctx.db.execute(select(WikiPage.id).where(WikiPage.user_id == user_id, WikiPage.status == "dirty"))
        .scalars()
        .all()
    )
    for page_id in dirty:
        # Debounced: a plan that dirties ten pages still compiles each once.
        ctx.enqueue("compile_wiki_page", {"page_id": page_id}, dedupe_key=f"wiki:{page_id}")
    return {**result.as_dict(), "pages_queued": len(dirty)}


@job_handler("compile_wiki_page")
def compile_wiki_page_job(ctx: JobContext, payload: dict) -> dict:
    """Compile one page, relink it, then queue a lint pass."""
    from app.models.wiki import WikiPage
    from app.wiki.compiler import compile_page
    from app.wiki.links import rebuild_links

    page_id = payload.get("page_id")
    if page_id is None:
        return {"skipped": "no page_id in payload"}
    page = ctx.db.get(WikiPage, int(page_id))
    if page is None:
        return {"skipped": f"page {page_id} not found"}

    result = compile_page(ctx.db, page, force=bool(payload.get("force")))
    rebuild_links(ctx.db, page)
    ctx.enqueue("lint_wiki", {"user_id": str(page.user_id)}, dedupe_key=f"lint:{page.user_id}")
    return {**result, "page": page.slug}


@job_handler("lint_wiki")
def lint_wiki_job(ctx: JobContext, payload: dict) -> dict:
    import uuid

    from app.wiki.lint import lint_wiki

    raw = payload.get("user_id")
    if not raw:
        return {"skipped": "no user_id in payload"}
    return lint_wiki(ctx.db, uuid.UUID(str(raw)))


__all__ = ["compile_wiki_page_job", "lint_wiki_job", "plan_topics_job"]
