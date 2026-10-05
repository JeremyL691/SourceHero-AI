"""RSS/Atom extraction.

Entries are fetched concurrently (4 workers) so a large feed does not take
`len(entries) * per_host_interval` seconds. An entry whose full text cannot be fetched
falls back to its summary -- a partial document beats a missing one, and the warning
records which it was.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.ingestion.fetch import FetchError, fetch
from app.ingestion.types import ExtractedDocument, detect_language
from app.ingestion.webpage import extract_webpage

log = get_logger(__name__)

MAX_WORKERS = 4
_ENTRY_TIME_KEYS = (
    "published_parsed",
    "updated_parsed",
    "created_parsed",
    "published",
    "updated",
    "created",
)
_TITLE_TIME_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def extract_feed(url: str, *, limit: int = 50) -> list[ExtractedDocument]:
    """Fetch a feed and return one document per entry."""
    result = fetch(url)
    feed = _parse(result.content)
    entries = _entries(feed)[:limit]
    if not entries:
        return []

    documents: list[ExtractedDocument | None] = [None] * len(entries)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_entry_document, entry, i): i for i, entry in enumerate(entries)}
        for future, index in futures.items():
            try:
                documents[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad entry must not kill the feed
                log.warning("rss entry %d failed: %s", index, exc)
                documents[index] = None
    return [d for d in documents if d is not None]


def _parse(content: bytes) -> Any:
    """feedparser first; fall back to ElementTree for feeds feedparser rejects."""
    try:
        import feedparser

        parsed = feedparser.parse(content)
        if parsed is not None and getattr(parsed, "entries", None):
            return parsed
        if parsed is not None and getattr(parsed, "bozo", 0) is False:
            return parsed
    except Exception:  # noqa: BLE001 - fall through to ElementTree
        log.debug("feedparser failed; trying ElementTree", exc_info=True)

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(content)
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if items:
            return _ElementTreeFeed(items, root)
    except Exception:  # noqa: BLE001 - an unparseable feed is simply empty
        log.debug("ElementTree feed parse failed", exc_info=True)
    return None


class _ElementTreeFeed:
    """The slice of the feedparser API this module uses, backed by ElementTree."""

    def __init__(self, items: list, root) -> None:
        self._items = items
        self._root = root

    @property
    def entries(self) -> list:
        return self._items


def _entries(feed: Any) -> list:
    return list(getattr(feed, "entries", []) or [])


def _first(entry: Any, *names: str) -> str | None:
    for name in names:
        try:
            value = entry.get(name)
        except Exception:  # noqa: BLE001 - ElementTree elements raise on .get
            value = None
        if not value:
            continue
        if isinstance(value, str):
            return value.strip()
        # feedparser sometimes returns a list of fragments.
        if isinstance(value, (list, tuple)) and value:
            return str(value[0]).strip()
        return str(value).strip()
    return None


def _entry_time(entry: Any) -> datetime | None:
    for key in _ENTRY_TIME_KEYS:
        try:
            value = entry.get(key)
        except Exception:  # noqa: BLE001
            value = None
        if not value:
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, (tuple, list)):
            try:
                import calendar

                ts = calendar.timegm(value)
                return datetime.fromtimestamp(ts, tz=UTC)
            except Exception:
                pass
        parsed = _parse_time(str(value))
        if parsed is not None:
            return parsed
    return None


def _parse_time(text: str) -> datetime | None:
    import email.utils

    text = text.strip()
    if not text:
        return None

    # 1. RFC 822 / 2822 format (e.g. "Mon, 01 Jan 2024 10:00:00 +0000")
    try:
        dt = email.utils.parsedate_to_datetime(text)
        if dt is not None:
            return dt.astimezone(UTC)
    except Exception:
        pass

    # 2. ISO 8601 format (e.g. "...Z", "...+00:00")
    try:
        iso_str = text.replace("Z", "+00:00").replace("z", "+00:00")
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass

    return None


def _summary(entry: Any) -> str:
    """Strip tags from an RSS summary/content block."""
    raw = _first(entry, "summary", "description", "subtitle") or ""
    if not raw:
        return ""
    from bs4 import BeautifulSoup

    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    return text


def _entry_document(entry: Any, index: int) -> ExtractedDocument | None:
    link = _first(entry, "link", "id", "guid")
    title = _first(entry, "title") or ""
    summary = _summary(entry)
    published = _entry_time(entry)
    warnings: list[str] = []

    text = ""
    if link and link.startswith(("http://", "https://")):
        try:
            page = fetch(link)
            doc = extract_webpage(page.content.decode("utf-8", errors="replace"), page.final_url)
            if doc.text.strip():
                text = doc.text
            if published is None:
                published = doc.published_at
            if not title:
                title = doc.title
        except FetchError as exc:
            warnings.append(f"entry_fetch_failed:{exc.code}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"entry_extract_failed:{type(exc).__name__}")

    if not text:
        # The summary is a real, citable extract of what the feed asserted.
        text = summary
        if text:
            warnings.append("summary_only")

    if not text.strip():
        return None

    return ExtractedDocument(
        title=title,
        text=text,
        url=link,
        published_at=published,
        lang=detect_language(text),
        raw_text=None,
        warnings=warnings,
    )


__all__ = ["MAX_WORKERS", "extract_feed"]
