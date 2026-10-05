"""Source CRUD.

A source is *where material comes from*; a document is the material. Keeping them
separate is what lets one URL produce many versions and one RSS feed produce many
documents.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.errors import DomainValidationError, NotFoundError
from app.ingestion.canonical import canonicalize_url
from app.models.source import SOURCE_TYPES, Source

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_EXTENSIONS = {".pdf", ".md", ".txt", ".html", ".htm"}
UPLOAD_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
}


def validate_source_type(source_type: str) -> str:
    if source_type not in SOURCE_TYPES:
        raise DomainValidationError(
            f"source_type must be one of: {', '.join(SOURCE_TYPES)}", code="bad_source_type"
        )
    return source_type


def create_source(
    db: Session,
    user_id: uuid.UUID,
    *,
    source_type: str,
    name: str,
    url: str | None = None,
    config: dict | None = None,
    watch: bool = False,
    refresh_interval_minutes: int | None = None,
) -> Source:
    validate_source_type(source_type)
    name = (name or "").strip()
    if not name:
        raise DomainValidationError("source name is required", code="name_required")

    canonical = canonicalize_url(url) if url else None
    if source_type in ("webpage", "rss") and not canonical:
        raise DomainValidationError(f"a {source_type} source requires a url", code="url_required")

    existing = (
        db.execute(
            select(Source).where(
                Source.user_id == user_id,
                Source.canonical_url == canonical,
                Source.source_type == source_type,
            )
        ).scalar_one_or_none()
        if canonical
        else None
    )
    if existing is not None:
        return existing

    source = Source(
        user_id=user_id,
        source_type=source_type,
        name=name,
        url=url,
        canonical_url=canonical,
        config=config or {},
        watch=watch,
        refresh_interval_minutes=refresh_interval_minutes,
        next_refresh_at=clock.utcnow() if watch else None,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def list_sources(
    db: Session,
    user_id: uuid.UUID,
    *,
    source_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Source]:
    stmt = select(Source).where(Source.user_id == user_id)
    if source_type:
        stmt = stmt.where(Source.source_type == source_type)
    stmt = stmt.order_by(Source.created_at.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).scalars())


def get_source(db: Session, user_id: uuid.UUID, source_id: uuid.UUID) -> Source:
    source = db.get(Source, source_id)
    if source is None or source.user_id != user_id:
        # 404 rather than 403: a 403 would confirm that another user's source exists.
        raise NotFoundError("source not found")
    return source


def update_source(db: Session, user_id: uuid.UUID, source_id: uuid.UUID, **changes) -> Source:
    source = get_source(db, user_id, source_id)
    if "name" in changes and changes["name"]:
        source.name = changes["name"].strip()
    if "url" in changes and changes["url"]:
        source.url = changes["url"]
        source.canonical_url = canonicalize_url(changes["url"])
    if "config" in changes and changes["config"] is not None:
        source.config = changes["config"]
    if "status" in changes and changes["status"] in ("active", "paused", "failed"):
        source.status = changes["status"]
    if "watch" in changes and changes["watch"] is not None:
        source.watch = bool(changes["watch"])
        if source.watch and source.next_refresh_at is None:
            source.next_refresh_at = clock.utcnow()
        elif not source.watch:
            source.next_refresh_at = None
    if "refresh_interval_minutes" in changes:
        source.refresh_interval_minutes = changes["refresh_interval_minutes"]
    db.commit()
    db.refresh(source)
    return source


def delete_source(db: Session, user_id: uuid.UUID, source_id: uuid.UUID) -> None:
    source = get_source(db, user_id, source_id)
    db.delete(source)  # documents cascade via the FK
    db.commit()


def store_upload(user_id: uuid.UUID, filename: str, data: bytes) -> tuple[str, str]:
    """Persist an upload and return `(storage_key, source_type)`.

    The key is content-addressed by sha256, so re-uploading the same file reuses the
    stored bytes instead of filling the disk with duplicates.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise DomainValidationError(
            f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB", code="file_too_large"
        )
    extension = Path(filename or "").suffix.lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise DomainValidationError(
            f"unsupported file type {extension or '(none)'}; allowed: {', '.join(sorted(UPLOAD_EXTENSIONS))}",
            code="bad_file_type",
        )
    digest = hashlib.sha256(data).hexdigest()
    key = f"uploads/{user_id}/{digest}{extension}"
    import io

    from app.storage import get_storage

    content_type = UPLOAD_CONTENT_TYPES.get(extension, "application/octet-stream")
    get_storage().upload(key, io.BytesIO(data), content_type)
    return key, ("pdf" if extension == ".pdf" else "file")


__all__ = [
    "MAX_UPLOAD_BYTES",
    "UPLOAD_EXTENSIONS",
    "create_source",
    "delete_source",
    "get_source",
    "list_sources",
    "store_upload",
    "update_source",
    "validate_source_type",
]
