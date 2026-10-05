"""Watches: standing questions the system keeps current."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.errors import DomainValidationError, NotFoundError
from app.indexing.embeddings import get_embedder
from app.models.source import Source
from app.models.temporal import Watch

KINDS = ("source", "query")
DEFAULT_REFRESH_MINUTES = 1440


def create_watch(
    db: Session,
    user_id: uuid.UUID,
    *,
    kind: str,
    source_id: uuid.UUID | None = None,
    query: str | None = None,
    refresh_interval_minutes: int | None = None,
) -> Watch:
    if kind not in KINDS:
        raise DomainValidationError(f"kind must be one of {', '.join(KINDS)}", code="bad_watch_kind")

    watch = Watch(user_id=user_id, kind=kind)
    if kind == "source":
        if source_id is None:
            raise DomainValidationError("a source watch needs source_id", code="missing_source_id")
        source = db.get(Source, source_id)
        if source is None or source.user_id != user_id:
            raise NotFoundError("source not found")
        watch.source_id = source.id
        watch.query = None
        # Watching a source is what makes the scheduler re-ingest it.
        source.watch = True
        source.next_refresh_at = clock.utcnow()
        if refresh_interval_minutes:
            source.refresh_interval_minutes = refresh_interval_minutes
        watch.created_at = clock.utcnow()
        watch.last_checked_at = None
    else:
        if not query or not query.strip():
            raise DomainValidationError("a query watch needs a query", code="missing_query")
        watch.query = query.strip()
        # Stored so "what changed about X?" becomes a kNN search rather than a
        # repeated exact-text lookup that misses every paraphrase.
        watch.embedding = get_embedder().embed_query(watch.query)

    db.add(watch)
    db.commit()
    db.refresh(watch)
    return watch


def list_watches(
    db: Session, user_id: uuid.UUID, *, limit: int | None = None, offset: int = 0
) -> list[Watch]:
    stmt = select(Watch).where(Watch.user_id == user_id).order_by(Watch.created_at).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def delete_watch(db: Session, user_id: uuid.UUID, watch_id: uuid.UUID) -> None:
    watch = db.get(Watch, watch_id)
    if watch is None or watch.user_id != user_id:
        raise NotFoundError("watch not found")
    if watch.source_id is not None:
        source = db.get(Source, watch.source_id)
        # Stop watching only when no other watch still wants the source.
        remaining = (
            db.execute(select(Watch).where(Watch.source_id == watch.source_id, Watch.id != watch.id))
            .scalars()
            .first()
        )
        if source is not None and remaining is None:
            source.watch = False
    db.delete(watch)
    db.commit()


def mark_checked(db: Session, watch: Watch, at: datetime | None = None) -> None:
    watch.last_checked_at = at or clock.utcnow()
    db.flush()


def next_refresh_at(watch: Watch, source: Source | None = None) -> datetime | None:
    """When this watch should next fire."""
    if watch.kind == "source":
        minutes = (source.refresh_interval_minutes if source else None) or DEFAULT_REFRESH_MINUTES
        return clock.utcnow() + timedelta(minutes=minutes)
    return None


__all__ = [
    "DEFAULT_REFRESH_MINUTES",
    "KINDS",
    "create_watch",
    "delete_watch",
    "list_watches",
    "mark_checked",
    "next_refresh_at",
]
