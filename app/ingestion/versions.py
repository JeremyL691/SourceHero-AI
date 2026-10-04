"""Document versioning.

This is where "a document changes over time" becomes real. One document identity
(`url:<canonical>`) can have many immutable versions; each version records *when its
text was true* in `effective_at`.

The rule that matters: `current_version_id` is the version with the greatest
`effective_at`, never the highest id. Backfilling an archived Wayback snapshot inserts
a version that is chronologically *earlier* than what we already have, and it must not
become "current".

Re-ingesting identical content is a no-op (`unchanged`), so a daily refresh of a static
page does not create noise.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.logging import get_logger
from app.core.text import normalize_text
from app.ingestion.canonical import identity_key
from app.ingestion.segment import split_sentences
from app.ingestion.types import ExtractedDocument
from app.models.document import Document, DocumentVersion
from app.models.source import Source

log = get_logger(__name__)

STATUS_NEW = "new"
STATUS_CHANGED = "changed"
STATUS_UNCHANGED = "unchanged"


@dataclass
class VersionResult:
    status: str  # "new" | "changed" | "unchanged"
    document: Document
    version: DocumentVersion | None  # None when unchanged
    previous: DocumentVersion | None

    @property
    def is_new_or_changed(self) -> bool:
        return self.status in (STATUS_NEW, STATUS_CHANGED)


def content_hash(clean_text: str) -> str:
    return hashlib.sha256(clean_text.encode("utf-8")).hexdigest()


def diff_sentences(previous_text: str, current_text: str) -> dict:
    """How many sentences were added and removed between two versions."""
    before = [s.text_of(previous_text) for s in split_sentences(previous_text)]
    after = [s.text_of(current_text) for s in split_sentences(current_text)]
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return {"sentences_added": added, "sentences_removed": removed}


def _previous_chronological(
    versions: list[DocumentVersion], effective_at: datetime
) -> DocumentVersion | None:
    """The version that was current just before `effective_at`."""
    earlier = [v for v in versions if v.effective_at < effective_at]
    if not earlier:
        return None
    return max(earlier, key=lambda v: v.effective_at)


def upsert_document_version(
    db: Session,
    *,
    source: Source,
    extracted: ExtractedDocument,
    origin: str = "live",
    effective_at: datetime | None = None,
    archived_at: datetime | None = None,
    fetched_at: datetime | None = None,
) -> VersionResult:
    """Store `extracted` as a new version of its document, or record it as unchanged.

    Idempotent: running it twice with the same content produces no second version.
    """
    clean_text = normalize_text(extracted.text or "")
    if not clean_text.strip():
        raise ValueError("refusing to store an empty version")

    raw_bytes = (extracted.raw_text or "").encode("utf-8") if extracted.url is None else None
    key = identity_key(url=extracted.url, clean_text=clean_text, raw_bytes=raw_bytes)
    now = clock.utcnow()

    # FOR UPDATE so two concurrent ingests of the same URL serialize here instead of
    # both deciding the document does not exist yet.
    document = db.execute(
        select(Document)
        .where(Document.user_id == source.user_id, Document.identity_key == key)
        .with_for_update()
    ).scalar_one_or_none()

    if document is None:
        document = Document(
            user_id=source.user_id,
            source_id=source.id,
            identity_key=key,
            title=extracted.title or "",
            url=extracted.url,
            canonical_url=extracted.url,
            author=extracted.author,
            published_at=extracted.published_at,
            lang=extracted.lang,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(document)
        db.flush()
    else:
        document.last_seen_at = now
        # Metadata that the new extraction knows better.
        if extracted.title and (not document.title or document.title != extracted.title):
            document.title = extracted.title
        if extracted.author:
            document.author = extracted.author
        if extracted.lang:
            document.lang = extracted.lang
        if extracted.published_at and document.published_at is None:
            document.published_at = extracted.published_at

    versions = list(
        db.execute(select(DocumentVersion).where(DocumentVersion.document_id == document.id)).scalars()
    )
    digest = content_hash(clean_text)

    existing = next((v for v in versions if v.content_hash == digest), None)
    if existing is not None:
        db.flush()
        return VersionResult(STATUS_UNCHANGED, document, None, existing)

    effective = effective_at or extracted.published_at or now
    # Two versions at the same instant make `effective_at` an ambiguous ordering key
    # (and `max()` between them arbitrary), so nudge forward until it is unique.
    taken = {v.effective_at for v in versions}
    while effective in taken:
        effective = _bump(effective, taken)

    previous = _previous_chronological(versions, effective)
    stats = diff_sentences(previous.clean_text, clean_text) if previous is not None else None

    version = DocumentVersion(
        user_id=source.user_id,
        document_id=document.id,
        version_no=(max((v.version_no for v in versions), default=0) + 1),
        content_hash=digest,
        clean_text=clean_text,
        raw_text=extracted.raw_text,
        page_map=extracted.page_map,
        origin=origin,
        effective_at=effective,
        fetched_at=fetched_at or now,
        archived_at=archived_at,
        diff_stats=stats,
    )
    db.add(version)
    db.flush()

    # `current_version_id` follows world time, not insertion order: a backfilled
    # archive is older than the live version and must not replace it.
    candidates = [*versions, version]
    current = max(candidates, key=lambda v: v.effective_at)
    document.current_version_id = current.id

    db.flush()
    log.debug(
        "versioned %s: %s (v%d, effective %s)",
        key,
        STATUS_NEW if previous is None else STATUS_CHANGED,
        version.version_no,
        version.effective_at.isoformat(),
    )
    return VersionResult(STATUS_NEW if previous is None else STATUS_CHANGED, document, version, previous)


def _bump(value: datetime, taken: set[datetime]) -> datetime:
    """The next representable instant after `value` that nothing else occupies."""
    from datetime import timedelta

    stepped = value
    for step in (timedelta(microseconds=1), timedelta(seconds=1), timedelta(minutes=1)):
        stepped = value + step
        if stepped not in taken:
            return stepped
    return stepped + timedelta(days=1)


def current_text(db: Session, document_id) -> str:
    """The text of a document's current version, or an empty string."""
    document = db.get(Document, document_id)
    if document is None or document.current_version_id is None:
        return ""
    version = db.get(DocumentVersion, document.current_version_id)
    return version.clean_text if version else ""


__all__ = [
    "STATUS_CHANGED",
    "STATUS_NEW",
    "STATUS_UNCHANGED",
    "VersionResult",
    "content_hash",
    "current_text",
    "diff_sentences",
    "upsert_document_version",
]
