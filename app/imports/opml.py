"""OPML subscription lists become RSS sources."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainValidationError
from app.imports import MAX_IMPORT_ITEMS, ImportError_, ImportResult
from app.ingestion.netguard import SSRFError, validate_url
from app.ingestion.sources import create_source
from app.models.source import Source


@dataclass
class OpmlFeed:
    name: str
    url: str


def normalize_feed_url(url: str) -> str:
    """Lowercase scheme and host, drop the fragment, strip a trailing slash."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def parse_opml(data: bytes) -> list[OpmlFeed]:
    """Every outline with an xmlUrl attribute. Raises `DomainValidationError` on bad XML."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise DomainValidationError(f"not a valid OPML document: {exc}", code="bad_opml") from exc

    outlines = list(root.iter("outline"))
    if len(outlines) > MAX_IMPORT_ITEMS:
        raise DomainValidationError(
            f"an OPML file may hold at most {MAX_IMPORT_ITEMS} outline entries", code="too_many_items"
        )

    feeds: list[OpmlFeed] = []
    for node in outlines:
        xml_url = (node.get("xmlUrl") or "").strip()
        if not xml_url:
            continue
        name = (node.get("text") or node.get("title") or "").strip() or xml_url
        feeds.append(OpmlFeed(name=name, url=xml_url))
    return feeds


def import_opml(db: Session, user_id: uuid.UUID, data: bytes) -> ImportResult:
    result = ImportResult()
    known = {
        normalize_feed_url(url)
        for (url,) in db.execute(
            select(Source.url).where(Source.user_id == user_id, Source.source_type == "rss")
        )
        if url
    }

    for feed in parse_opml(data):
        key = normalize_feed_url(feed.url)
        if key in known:
            result.skipped += 1
            continue
        try:
            validate_url(feed.url)
        except SSRFError as exc:
            result.errors.append(ImportError_(code=exc.code, url=feed.url))
            continue
        known.add(key)
        create_source(db, user_id, source_type="rss", name=feed.name, url=feed.url)
        result.created += 1
    return result


__all__ = ["OpmlFeed", "import_opml", "normalize_feed_url", "parse_opml"]
