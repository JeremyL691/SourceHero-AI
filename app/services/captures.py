from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.base import ExtractedDocument
from app.ingestion.chunking import clean_text, sha256_text
from app.ingestion.quality import store_extracted_documents
from app.models import Document, Source
from app.services.pipeline import create_source, ingest_source
from app.services.semantic_index import index_new_chunks


QUICK_CAPTURE_SOURCE_NAME = "Quick captures"
CAPTURE_KIND_EMPTY = "empty"
CAPTURE_KIND_URL_ONLY = "url_only"
CAPTURE_KIND_TEXT_ONLY = "text_only"
CAPTURE_KIND_URL_PLUS_EXCERPT = "url_plus_excerpt"
CAPTURE_KINDS = {
    CAPTURE_KIND_EMPTY,
    CAPTURE_KIND_URL_ONLY,
    CAPTURE_KIND_TEXT_ONLY,
    CAPTURE_KIND_URL_PLUS_EXCERPT,
}
_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


@dataclass
class CapturePreview:
    mode: str
    raw_text: str
    source_url: str | None
    excerpt_text: str
    suggested_title: str


def parse_capture_text(raw_text: str) -> CapturePreview:
    normalized = raw_text.replace("\r\n", "\n").strip()
    if not normalized:
        return CapturePreview(
            mode=CAPTURE_KIND_EMPTY,
            raw_text="",
            source_url=None,
            excerpt_text="",
            suggested_title="Quick capture",
        )

    source_url = _first_url(normalized)
    excerpt = _extract_excerpt(normalized)
    if source_url and not excerpt:
        return CapturePreview(
            mode=CAPTURE_KIND_URL_ONLY,
            raw_text=normalized,
            source_url=source_url,
            excerpt_text="",
            suggested_title=_suggested_title("", source_url),
        )

    mode = CAPTURE_KIND_URL_PLUS_EXCERPT if source_url and excerpt else CAPTURE_KIND_TEXT_ONLY
    return CapturePreview(
        mode=mode,
        raw_text=normalized,
        source_url=source_url,
        excerpt_text=excerpt if excerpt else normalized,
        suggested_title=_suggested_title(excerpt or normalized, source_url),
    )


def create_capture(
    db: Session,
    user_id: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    excerpt_text: str | None = None,
) -> dict:
    normalized_url = normalize_capture_url(source_url) if source_url else None
    cleaned_excerpt = clean_text(excerpt_text or "")
    final_title = _normalize_title(title, cleaned_excerpt, normalized_url)

    if normalized_url and not cleaned_excerpt:
        return _capture_url_only(db, user_id, normalized_url, final_title)
    if cleaned_excerpt:
        return _capture_excerpt(db, user_id=user_id, title=final_title, source_url=normalized_url, excerpt_text=cleaned_excerpt)
    raise ValueError("Capture requires either a URL or excerpt text.")


def normalize_capture_url(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    candidate = candidate.rstrip(".,);]}>")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only http and https URLs are supported for quick capture.")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def get_or_create_quick_capture_source(db: Session, user_id: str) -> tuple[Source, bool]:
    source = db.scalar(
        select(Source).where(
            Source.source_type == "clip",
            Source.name == QUICK_CAPTURE_SOURCE_NAME,
            Source.user_id == user_id,
        )
    )
    if source:
        return source, False
    return create_source(db, user_id, "clip", QUICK_CAPTURE_SOURCE_NAME), True


def _capture_url_only(db: Session, user_id: str, normalized_url: str, title: str) -> dict:
    source = _find_webpage_source_by_url(db, user_id, normalized_url)
    source_created = False
    if not source:
        source = create_source(db, user_id, "webpage", title, url=normalized_url)
        source_created = True
    if source.status == "paused":
        return {
            "status": "paused",
            "capture_kind": CAPTURE_KIND_URL_ONLY,
            "message": "This source already exists but is paused, so quick capture did not run ingestion.",
            "source_id": source.id,
            "source_created": source_created,
            "document_id": None,
            "document_created": False,
            "duplicate": not source_created,
            "ingestion_run_id": None,
            "ingestion_status": "paused",
            "documents_inserted": 0,
            "chunks_inserted": 0,
        }

    run = ingest_source(db, user_id, source.id)
    return {
        "status": "ingested" if run.status == "success" else "failed",
        "capture_kind": CAPTURE_KIND_URL_ONLY,
        "message": (
            f"Saved webpage source and indexed {run.chunks_inserted} chunks."
            if source_created
            else f"Reused webpage source and indexed {run.chunks_inserted} chunks."
        )
        if run.status == "success"
        else (run.error_message or f"Ingestion finished with status `{run.status}`."),
        "source_id": source.id,
        "source_created": source_created,
        "document_id": None,
        "document_created": False,
        "duplicate": not source_created,
        "ingestion_run_id": run.id,
        "ingestion_status": run.status,
        "documents_inserted": run.documents_inserted,
        "chunks_inserted": run.chunks_inserted,
    }


def _capture_excerpt(db: Session, *, user_id: str, title: str, source_url: str | None, excerpt_text: str) -> dict:
    source, source_created = get_or_create_quick_capture_source(db, user_id)
    content_hash = sha256_text(source_url, title, excerpt_text)
    existing_document = db.scalar(select(Document).where(Document.content_hash == content_hash))
    if existing_document:
        return {
            "status": "duplicate",
            "capture_kind": CAPTURE_KIND_URL_PLUS_EXCERPT if source_url else CAPTURE_KIND_TEXT_ONLY,
            "message": "That capture is already saved.",
            "source_id": source.id,
            "source_created": source_created,
            "document_id": existing_document.id,
            "document_created": False,
            "duplicate": True,
            "ingestion_run_id": None,
            "ingestion_status": None,
            "documents_inserted": 0,
            "chunks_inserted": 0,
        }

    stats = store_extracted_documents(
        db,
        source,
        [
            ExtractedDocument(
                title=title,
                raw_text=excerpt_text,
                clean_text=excerpt_text,
                url=source_url,
                metadata={"source_kind": "clip", "capture_url": source_url},
            )
        ],
        user_id=user_id,
    )
    db.commit()
    document = db.scalar(select(Document).where(Document.content_hash == content_hash))
    try:
        index_new_chunks(db, [chunk_id for chunk_id in stats.get("chunk_ids_inserted", []) if chunk_id is not None])
    except Exception:
        pass

    return {
        "status": "saved",
        "capture_kind": CAPTURE_KIND_URL_PLUS_EXCERPT if source_url else CAPTURE_KIND_TEXT_ONLY,
        "message": "Saved quick capture and indexed it for search.",
        "source_id": source.id,
        "source_created": source_created,
        "document_id": document.id if document else None,
        "document_created": bool(document),
        "duplicate": False,
        "ingestion_run_id": None,
        "ingestion_status": None,
        "documents_inserted": int(stats.get("documents_inserted", 0)),
        "chunks_inserted": int(stats.get("chunks_inserted", 0)),
    }


def _find_webpage_source_by_url(db: Session, user_id: str, normalized_url: str) -> Source | None:
    sources = db.scalars(
        select(Source).where(Source.source_type == "webpage", Source.user_id == user_id)
    ).all()
    for source in sources:
        if normalize_capture_url(source.url) == normalized_url:
            return source
    return None


def _extract_excerpt(text: str) -> str:
    return clean_text(_URL_RE.sub(" ", text))


def _first_url(text: str) -> str | None:
    for match in _URL_RE.finditer(text):
        candidate = match.group(0).rstrip(".,);]}>")
        try:
            return normalize_capture_url(candidate)
        except ValueError:
            continue
    return None


def _normalize_title(title: str | None, excerpt_text: str, source_url: str | None) -> str:
    candidate = clean_text(title or "")[:160]
    if candidate:
        return candidate
    return _suggested_title(excerpt_text, source_url)


def _suggested_title(excerpt_text: str, source_url: str | None) -> str:
    line = next((line.strip() for line in excerpt_text.splitlines() if line.strip()), "")
    if line:
        return line[:160]
    if source_url:
        parsed = urlsplit(source_url)
        slug = parsed.path.rstrip("/").split("/")[-1] if parsed.path and parsed.path != "/" else ""
        if slug:
            return slug.replace("-", " ").replace("_", " ")[:160]
        return parsed.netloc[:160]
    return "Quick capture"
