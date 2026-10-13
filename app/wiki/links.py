"""Rebuilding the page link graph.

Links come from `see_also` plus any other page whose label or entity key appears in
this page's body. Rebuilt wholesale on every compile: an incremental union would keep
links to pages that no longer mention each other.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claim import Claim
from app.models.wiki import Topic, WikiLink, WikiPage, WikiPageClaim
from app.wiki.topics import entity_keys, slugify


def page_entities(db: Session, page: WikiPage) -> set[str]:
    """Entity keys mentioned anywhere on this page."""
    statements = (
        db.execute(
            select(Claim.statement)
            .join(WikiPageClaim, WikiPageClaim.claim_id == Claim.id)
            .where(WikiPageClaim.page_id == page.id)
        )
        .scalars()
        .all()
    )
    keys: set[str] = set()
    for statement in statements:
        if statement:
            keys |= entity_keys(Claim(statement=statement))
    return keys


def rebuild_links(db: Session, page: WikiPage) -> int:
    """Replace this page's outgoing links; return how many it now has."""
    topic = db.get(Topic, page.topic_id)
    mine = page_entities(db, page)
    if topic is not None:
        mine.add(slugify(topic.label))

    for existing in db.execute(select(WikiLink).where(WikiLink.from_page_id == page.id)).scalars():
        db.delete(existing)
    db.flush()

    if not mine:
        return 0

    others = (
        db.execute(select(WikiPage).where(WikiPage.user_id == page.user_id, WikiPage.id != page.id))
        .scalars()
        .all()
    )
    if not others:
        return 0

    other_topic_ids = {o.topic_id for o in others if o.topic_id is not None}
    topics_by_id = {}
    if other_topic_ids:
        topics_by_id = {
            t.id: t for t in db.execute(select(Topic).where(Topic.id.in_(other_topic_ids))).scalars()
        }

    other_page_ids = [o.id for o in others]
    statements_by_page: dict[int, list[str]] = {o.id: [] for o in others}
    claim_rows = db.execute(
        select(WikiPageClaim.page_id, Claim.statement)
        .join(Claim, WikiPageClaim.claim_id == Claim.id)
        .where(WikiPageClaim.page_id.in_(other_page_ids))
    ).all()
    for pid, stmt_text in claim_rows:
        if stmt_text:
            statements_by_page[pid].append(stmt_text)

    written = 0
    for other in others:
        keys: set[str] = set()
        for statement in statements_by_page[other.id]:
            keys |= entity_keys(Claim(statement=statement))
        other_topic = topics_by_id.get(other.topic_id)
        if other_topic is not None:
            keys.add(slugify(other_topic.label))
        if mine & keys:
            db.add(WikiLink(from_page_id=page.id, to_page_id=other.id, anchor=other.title))
            written += 1
    db.flush()
    return written


def backlinks(db: Session, page: WikiPage) -> list[WikiPage]:
    """Pages that link here."""
    ids = db.execute(select(WikiLink.from_page_id).where(WikiLink.to_page_id == page.id)).scalars().all()
    if not ids:
        return []
    return list(db.execute(select(WikiPage).where(WikiPage.id.in_(ids))).scalars())


__all__ = ["backlinks", "page_entities", "rebuild_links"]
