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


@job_handler("seed_demo_corpus")
def seed_demo_corpus_job(ctx: JobContext, payload: dict) -> dict:
    """Seed the demo corpus, then optionally run the pipeline to completion.

    `drain` is explicit. Draining inside the job is right for `make demo`, which runs a
    single worker and wants a finished corpus when it returns -- but wrong when a
    separate worker is already consuming the queue: two processes then index, cluster
    and plan the same corpus at once, which deadlocked on `claim_clusters`. The HTTP
    endpoint therefore queues and lets the real worker do the work.
    """
    import uuid as _uuid

    from app.demo.seed import drain, seed_demo, summary
    from app.ingestion.pipeline import ensure_user

    raw = payload.get("user_id") or str(ctx.user_id)
    user_id = _uuid.UUID(str(raw))
    ensure_user(ctx.db, _email_for(ctx.db, user_id), "demo-password-123")
    seed_demo(ctx.db, user_id)
    ctx.db.commit()

    processed = 0
    if payload.get("drain", False):
        processed = drain(ctx.db)
        ctx.db.commit()
    else:
        ctx.db.commit()
    return {"jobs_drained": processed, **summary(ctx.db, user_id)}


def _email_for(db, user_id):
    from app.models.user import User

    user = db.get(User, user_id)
    return user.email if user is not None else f"demo-{user_id}@sourcehero.local"


__all__ = ["compile_wiki_page_job", "lint_wiki_job", "plan_topics_job", "seed_demo_corpus_job"]
