"""Webpage extraction.

Primary path is `trafilatura`, which strips navigation, boilerplate and ads far more
reliably than a hand-rolled parser. When it returns nothing (a JS-only page, an
unusual markup), we fall back to BeautifulSoup so a page is never silently empty --
silence here would look like "the source had no claims", which is a different claim
about the world.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.ingestion.types import ExtractedDocument, detect_language

log = get_logger(__name__)

_BOILERPLATE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "noscript")
# Below this, an extraction is a fragment or a navigation label, not an article.
MIN_PROSE_CHARS = 40
_MAIN_SELECTORS = ("article", "main", '[role="main"]', ".post-content", ".entry-content", "#content")


def extract_webpage(html: str, url: str | None = None) -> ExtractedDocument:
    """Extract one document from an HTML string."""
    doc = _trafilatura(html, url)
    if doc is None or not doc.text.strip():
        fallback = _bs4(html, url=url)
        # Keep trafilatura's metadata even when falling back for the body.
        if doc is not None and not doc.text.strip() and fallback.text.strip():
            fallback.title = fallback.title or doc.title
            fallback.published_at = fallback.published_at or doc.published_at
            fallback.url = url or doc.url
            return fallback
        return fallback
    return doc


def _as_dict(raw: Any) -> dict:
    """trafilatura >=2 returns a `Document`; earlier versions return a dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    as_dict = getattr(raw, "as_dict", None)
    if callable(as_dict):
        try:
            return as_dict() or {}
        except Exception:  # noqa: BLE001 - fall through to attribute access
            pass
    return {
        "title": getattr(raw, "title", None),
        "author": getattr(raw, "author", None),
        "date": getattr(raw, "date", None),
        "text": getattr(raw, "text", None),
        "hostname": getattr(raw, "hostname", None),
    }


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    # ISO-8601 as used by <time datetime> and article:published_time: a trailing "Z"
    # and fractional seconds both need to come off before strptime, or the markup path
    # silently falls through to the extractor's guess.
    iso = text[:-1] if text.endswith(("Z", "z")) else text
    iso = iso.split(".")[0].split("+")[0]
    # trafilatura emits YYYY-MM-DD (possibly with a time part) when it can.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(iso, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    # No dateutil dependency: an unparseable date is simply absent, which is the
    # honest outcome -- guessing would put a wrong claim about publication time into
    # the version timeline, where it drives claim lifecycle.
    return None


# Metadata that actually states when the document was published.
_DATE_META_KEYS = (
    "article:published_time",
    "og:published_time",
    "date",
    "dc.date",
    "dc.date.issued",
    "sailthru.date",
)
_DATE_ATTRS = ("content", "datetime")


def _explicit_date(html: str) -> datetime | None:
    """A publication date stated in the markup, or None.

    Checked before the extractor's guess because `published_at` seeds
    `version.effective_at`, which is what drives claim lifecycle -- an invented date
    would silently change which claims are superseded.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 - malformed markup falls back to the guess
        return None

    for key in _DATE_META_KEYS:
        # `soup.find(...) or soup.find(...)` would be wrong here: a <meta> element has
        # no children, so BeautifulSoup considers it falsy and the first hit would be
        # discarded. Test against None explicitly.
        tags = [soup.find("meta", attrs={"property": key}), soup.find("meta", attrs={"name": key})]
        for tag in tags:
            if tag is None:
                continue
            for attr in _DATE_ATTRS:
                parsed = _parse_date(tag.get(attr))
                if parsed is not None:
                    return parsed

    for tag in soup.find_all("time"):
        parsed = _parse_date(tag.get("datetime"))
        if parsed is not None:
            return parsed
    return None


def _looks_like_prose(text: str, min_chars: int = MIN_PROSE_CHARS) -> bool:
    """Does this look like document body text rather than leftover chrome?"""
    if len(text) < min_chars:
        return False
    return any(ch in text for ch in ".!?。！？;；\n")


def _trafilatura(html: str, url: str | None) -> ExtractedDocument | None:
    try:
        import trafilatura
    except ImportError:  # pragma: no cover - trafilatura is a hard dependency
        return None
    try:
        raw = trafilatura.bare_extraction(
            html,
            url=url,
            with_metadata=True,
            include_comments=False,
            favor_recall=True,
        )
    except Exception:  # noqa: BLE001 - never let the parser kill an ingest
        log.debug("trafilatura failed for %s", url, exc_info=True)
        return None

    data = _as_dict(raw)
    text = (data.get("text") or "").strip()
    if not text:
        return None

    # `favor_recall=True` is deliberate -- we would rather keep a real sentence we
    # might have missed than drop it -- but it also admits chrome: on a page with no
    # article, it happily returns the navigation ("Home"). Prose contains sentence
    # punctuation; a bare fragment does not, so treat that as "no article found" and
    # let the structure-aware BeautifulSoup pass decide.
    if not _looks_like_prose(text):
        log.debug("trafilatura returned chrome rather than prose for %s", url)
        return None

    warnings: list[str] = []
    # Explicit metadata in the markup is evidence; trafilatura's `date` is a guess
    # (it will read a year out of a "Copyright 2026" footer). Prefer the evidence.
    # Only markup counts. trafilatura's `date`/`filedate` are guesses: on a real page
    # they came out as a year cited in a reference (K7.6: en.wikipedia "Lithium-ion
    # battery" yielded 2003-03-26), which would be stored as a publication date.
    published = _explicit_date(html)
    if published is not None:
        warnings.append("published_at_from_markup")

    return ExtractedDocument(
        title=(data.get("title") or "").strip(),
        text=text,
        url=url,
        author=(data.get("author") or None),
        published_at=published,
        lang=detect_language(text),
        raw_text=html,
        warnings=warnings,
    )


def _bs4(html: str, url: str | None = None) -> ExtractedDocument:
    """A conservative fallback: main content if we can find it, else all paragraphs."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_BOILERPLATE_TAGS):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title and soup.h1:
        title = soup.h1.get_text(strip=True)

    root = None
    for selector in _MAIN_SELECTORS:
        try:
            found = soup.select_one(selector)
        except Exception:  # noqa: BLE001 - a malformed selector must not abort
            continue
        if found is not None:
            root = found
            break
    if root is None:
        root = soup.body or soup

    # Paragraphs keep the reading order and the sentence boundaries intact, which
    # matters because every downstream offset is measured against this text.
    blocks = [p.get_text(" ", strip=True) for p in root.find_all(["p", "li", "h1", "h2", "h3"])]
    text = "\n\n".join(b for b in blocks if b)

    return ExtractedDocument(
        title=title,
        text=text,
        url=url,
        lang=detect_language(text),
        raw_text=html,
        warnings=["fallback_extractor"] if text else ["no_text_found"],
    )


__all__ = ["extract_webpage"]
