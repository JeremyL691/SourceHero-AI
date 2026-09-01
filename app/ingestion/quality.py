from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.base import ExtractedDocument
from app.ingestion.chunking import chunk_text, estimate_tokens, sha256_text
from app.models import Document, DocumentChunk, Source


def store_extracted_documents(
    db: Session,
    source: Source,
    extracted: list[ExtractedDocument],
    user_id: str | None = None,
) -> dict[str, object]:
    stats = {
        "documents_found": len(extracted),
        "documents_inserted": 0,
        "chunks_inserted": 0,
        "duplicates_skipped": 0,
        "chunk_ids_inserted": [],
    }

    effective_user_id = user_id or source.user_id

    for doc in extracted:
        if not doc.clean_text:
            # Empty extractions carry no searchable content; counting them under
            # duplicates_skipped keeps ingestion stats conservative and avoids
            # inserting zero-length documents.
            stats["duplicates_skipped"] += 1
            continue
        content_hash = sha256_text(doc.url, doc.title, doc.clean_text)
        exists = db.scalar(
            select(Document.id).where(
                Document.content_hash == content_hash,
                Document.user_id == effective_user_id,
            )
        )
        if exists:
            stats["duplicates_skipped"] += 1
            continue

        db_doc = Document(
            source_id=source.id,
            user_id=effective_user_id,
            title=doc.title[:512],
            url=doc.url,
            author=doc.author,
            published_at=doc.published_at,
            content_hash=content_hash,
            raw_text=doc.raw_text,
            clean_text=doc.clean_text,
        )
        db.add(db_doc)
        db.flush()
        stats["documents_inserted"] += 1

        for index, chunk in enumerate(chunk_text(doc.clean_text)):
            metadata = dict(doc.metadata)
            metadata.update({"title": doc.title, "url": doc.url, "source_id": source.id})
            # Chunk hash includes source_type so the same text captured via
            # different source kinds (e.g., webpage vs clip) stays isolated;
            # cross-source dedup of identical payloads is intentionally not merged.
            chunk_hash = sha256_text(source.source_type, doc.url, doc.title, chunk)
            if db.scalar(
                select(DocumentChunk.id).where(
                    DocumentChunk.chunk_hash == chunk_hash,
                    DocumentChunk.user_id == effective_user_id,
                )
            ):
                stats["duplicates_skipped"] += 1
                continue
            db_chunk = DocumentChunk(
                document_id=db_doc.id,
                user_id=effective_user_id,
                chunk_index=index,
                chunk_text=chunk,
                chunk_hash=chunk_hash,
                token_estimate=estimate_tokens(chunk),
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                # embedding_id stays NULL until an embedding is actually persisted.
                # The pgvector column (schema.sql) is the next milestone; lexical search
                # is the live path. See `app/services/semantic_index.py`.
                embedding_id=None,
            )
            db.add(db_chunk)
            db.flush()
            stats["chunk_ids_inserted"].append(db_chunk.id)
            stats["chunks_inserted"] += 1

    return stats
