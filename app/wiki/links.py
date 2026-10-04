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
    ids = db.execute(select(WikiPageClaim.claim_id).where(WikiPageClaim.page_id == page.id)).scalars().all()
    if not ids:
        return set()
    keys: set[str] = set()
    for (statement,) in db.execute(select(Claim.statement).where(Claim.id.in_(ids))).all():
        keys |= entity_keys(Claim(statement=statement or ""))
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

    written = 0
    for other in others:
        keys = page_entities(db, other)
        other_topic = db.get(Topic, other.topic_id)
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
