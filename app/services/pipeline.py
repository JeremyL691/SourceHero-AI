from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.pdf import ingest_pdf
from app.ingestion.quality import store_extracted_documents
from app.ingestion.rss import ingest_rss
from app.ingestion.webpage import ingest_webpage
from app.models import Document, DocumentChunk, IngestionRun, Source, utc_now
from app.services.library import cleanup_item_links
from app.services.semantic_index import index_new_chunks, remove_chunk_embeddings

logger = logging.getLogger(__name__)

INGESTABLE_SOURCE_TYPES = {"rss", "webpage", "pdf"}
SUPPORTED_SOURCE_TYPES = INGESTABLE_SOURCE_TYPES | {"conversation", "clip"}


class SourcePausedError(Exception):
    pass


def _sanitize_error(exc: Exception) -> str:
    logger.exception("Ingestion failed", exc_info=exc)
    text = str(exc).strip()
    lowered = text.lower()

    if "timeout" in lowered:
        return "Request timed out."
    if "name or service not known" in lowered:
        return "DNS lookup failed."
    if "ssl" in lowered or "certificate" in lowered:
        return "TLS error."
    if "encrypted" in lowered and "pdf" in lowered:
        return "PDF is encrypted."
    if "connection" in lowered:
        return "Could not reach the host."

    short = text.splitlines()[0][:200]
    return short or exc.__class__.__name__


def create_source(
    db: Session,
    user_id: str,
    source_type: str,
    name: str,
    url: str | None = None,
    local_path: str | None = None,
    r2_key: str | None = None,
) -> Source:
    if source_type in {"rss", "webpage"} and not url:
        raise ValueError(f"{source_type} source requires a URL")
    if source_type == "pdf" and not local_path and not r2_key:
        raise ValueError("pdf source requires local_path or r2_key")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"Unsupported source type: {source_type}")
    source = Source(
        user_id=user_id,
        source_type=source_type,
        name=name,
        url=url,
        local_path=local_path,
        r2_key=r2_key,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def update_source(
    db: Session,
    user_id: str,
    source_id: int,
    name: str | None = None,
    url: str | None = None,
    local_path: str | None = None,
    r2_key: str | None = None,
    status: str | None = None,
) -> Source:
    source = db.get(Source, source_id)
    if not source:
        raise ValueError(f"Source not found: {source_id}")
    if source.user_id != user_id:
        raise ValueError(f"Source not found: {source_id}")
    if name is not None:
        source.name = name
    if url is not None:
        source.url = url
    if local_path is not None:
        source.local_path = local_path
    if r2_key is not None:
        source.r2_key = r2_key
    if status is not None:
        source.status = status
    if source.source_type in {"rss", "webpage"} and not source.url:
        raise ValueError(f"{source.source_type} source requires a URL")
    if source.source_type == "pdf" and not source.local_path and not source.r2_key:
        raise ValueError("pdf source requires local_path or r2_key")
    db.commit()
    db.refresh(source)
    return source


def delete_source(db: Session, user_id: str, source_id: int) -> None:
    source = db.get(Source, source_id)
    if not source:
        raise ValueError(f"Source not found: {source_id}")
    if source.user_id != user_id:
        raise ValueError(f"Source not found: {source_id}")
    document_ids = db.scalars(select(Document.id).where(Document.source_id == source_id)).all()
    chunk_ids = db.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id.in_(document_ids))).all() if document_ids else []
    cleanup_item_links(db, "source", [source_id])
    cleanup_item_links(db, "document", list(document_ids))
    db.delete(source)
    db.commit()
    remove_chunk_embeddings(list(chunk_ids))


def ingest_source(db: Session, user_id: str, source_id: int) -> IngestionRun:
    source = db.get(Source, source_id)
    if not source:
        raise ValueError(f"Source not found: {source_id}")
    if source.user_id != user_id:
        raise ValueError(f"Source not found: {source_id}")
    if source.source_type == "clip":
        raise ValueError("Clip sources are managed by Quick Capture and do not support ingestion.")
    if source.status == "paused":
        raise SourcePausedError(
            f"Source {source_id} ({source.name!r}) is paused. Activate it before running ingestion."
        )

    previous_status = source.status
    run = IngestionRun(source_id=source.id, user_id=user_id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        if source.source_type == "rss":
            docs = ingest_rss(source.url or "", fetch_full_articles=True)
        elif source.source_type == "webpage":
            docs = ingest_webpage(source.url or "")
        elif source.source_type == "pdf":
            pdf_path = source.local_path or ""
            if source.r2_key and not source.local_path:
                from app.storage import get_storage

                storage = get_storage()
                pdf_bytes = storage.download(source.r2_key)
                import tempfile

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    pdf_path = tmp.name
            docs = ingest_pdf(pdf_path)
        elif source.source_type == "conversation":
            docs = []
        else:
            raise ValueError(f"Unsupported source type: {source.source_type}")

        stats = store_extracted_documents(db, source, docs, user_id=user_id)
        run.documents_found = stats["documents_found"]
        run.documents_inserted = stats["documents_inserted"]
        run.chunks_inserted = stats["chunks_inserted"]
        run.duplicates_skipped = stats["duplicates_skipped"]
        run.status = "success"
        run.ended_at = utc_now()
        source.last_ingested_at = run.ended_at
        if previous_status != "paused":
            source.status = "active"
        db.commit()
        try:
            index_new_chunks(db, [chunk_id for chunk_id in stats.get("chunk_ids_inserted", []) if chunk_id is not None])
        except Exception as exc:
            logger.warning("Semantic indexing failed after ingest for source %s: %s", source_id, exc)
    except Exception as exc:
        db.rollback()
        run = db.get(IngestionRun, run.id)
        source = db.get(Source, source_id)
        if run:
            run.status = "failed"
            run.ended_at = utc_now()
            run.error_message = _sanitize_error(exc)
        if source and source.status != "paused" and previous_status != "paused":
            source.status = "failed"
        db.commit()
    db.refresh(run)
    return run


def platform_stats(db: Session, user_id: str) -> dict[str, int]:
    return {
        "sources": db.scalar(select(func.count(Source.id)).where(Source.user_id == user_id)) or 0,
        "documents": db.scalar(select(func.count(Document.id)).where(Document.user_id == user_id)) or 0,
        "chunks": db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.user_id == user_id)) or 0,
        "ingestion_runs": db.scalar(select(func.count(IngestionRun.id)).where(IngestionRun.user_id == user_id)) or 0,
    }
