"""PDF and plain-text extraction.

A PDF becomes ONE document, not one document per page: the claim ledger reasons about
claims, and a report split across pages would fragment the sentences that matter.
`page_map` keeps the offsets back to the original page so the reader can still
highlight the right place.
"""

from __future__ import annotations

import io
import re

from app.core.logging import get_logger
from app.core.text import normalize_text
from app.ingestion.fetch import FetchError
from app.ingestion.types import ExtractedDocument, detect_language
from app.ingestion.webpage import extract_webpage

log = get_logger(__name__)

# Below this many characters per page, the PDF almost certainly has no text layer
# (it is a scan) and the extraction is worthless -- say so rather than storing noise.
MIN_CHARS_PER_PAGE = 20
PAGE_SEPARATOR = "\n\n"


def extract_pdf(data: bytes, *, url: str | None = None, title: str | None = None) -> ExtractedDocument:
    """Extract one document from PDF bytes."""
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many PDFs are "encrypted" with an empty owner password; try that first.
            try:
                if reader.decrypt("") == 0:
                    raise FetchError("unsupported_scheme", "PDF is password protected")
            except FetchError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise FetchError("unsupported_scheme", f"PDF is password protected: {exc}") from exc
    except FetchError:
        raise
    except PyPdfError as exc:
        # PyPdfError is the base class: EmptyFileError, PdfStreamError and
        # PdfReadError all derive from it, and they are raised for very different
        # malformed inputs. Catching only PdfReadError lets an empty upload escape
        # as an unhandled exception instead of a recorded ingest failure.
        raise FetchError("unsupported_scheme", f"unreadable PDF: {exc}") from exc

    parts: list[str] = []
    page_map: list[dict] = []
    cursor = 0
    total_chars = 0

    for number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the document
            log.debug("pdf page %d extraction failed: %s", number, exc)
            page_text = ""
        norm_page = normalize_text(page_text)
        if not norm_page:
            continue
        if parts:
            cursor += len(PAGE_SEPARATOR)
        start = cursor
        parts.append(norm_page)
        cursor = start + len(norm_page)
        page_map.append({"page": number, "start": start, "end": cursor})
        total_chars += len(page_text)

    text = PAGE_SEPARATOR.join(parts)
    page_count = len(reader.pages)
    warnings: list[str] = []
    if not text.strip() or page_count and total_chars / page_count < MIN_CHARS_PER_PAGE:
        warnings.append("no_text_layer")

    meta_title = title or ""
    if not meta_title:
        try:
            meta_title = (reader.metadata.title if reader.metadata else "") or ""
        except Exception:  # noqa: BLE001
            meta_title = ""

    return ExtractedDocument(
        title=meta_title.strip(),
        text=text,
        url=url,
        published_at=None,
        lang=detect_language(text),
        page_map=page_map or None,
        warnings=warnings,
        raw_bytes=data,
    )


def extract_text_file(
    data: bytes, *, filename: str = "", url: str | None = None, title: str | None = None
) -> ExtractedDocument:
    """Markdown, plain text, or HTML -- routed by extension."""
    name = filename.lower()
    if name.endswith((".html", ".htm", ".xhtml")):
        doc = extract_webpage(data.decode("utf-8", errors="replace"), url)
    else:
        text = _decode(data)
        doc = ExtractedDocument(text=text, url=url, lang=detect_language(text))
        doc.title = title or _first_markdown_heading(text)

    doc.raw_bytes = data
    if title:
        doc.title = title
    elif not doc.title:
        doc.title = filename.rsplit("/", 1)[-1]
    return doc


def _decode(data: bytes) -> str:
    """Decode bytes as text, trying the encodings real documents actually use."""
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_HEADING = re.compile(r"^#{1,2}\s+(.+)$", re.MULTILINE)


def _first_markdown_heading(text: str) -> str:
    match = _HEADING.search(text or "")
    return match.group(1).strip() if match else ""


__all__ = ["MIN_CHARS_PER_PAGE", "PAGE_SEPARATOR", "extract_pdf", "extract_text_file"]
