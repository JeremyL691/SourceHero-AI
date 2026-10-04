"""Writing a version's chunks and embeddings.

Idempotent by design: re-running on a version that already has chunks *fills in what
is missing* rather than deleting and rebuilding. A rebuild would cascade away the
claims and user labels attached to those chunks, which by M8 are the most valuable
data in the system.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.indexing.embeddings import get_embedder
from app.indexing.tokenize import search_text as build_search_text
from app.ingestion.chunking import chunk_spans
from app.models.chunk import Chunk
from app.models.document import DocumentVersion

log = get_logger(__name__)

EMBED_BATCH = 64


def chunk_hash(text: str, version_id: int, index: int) -> str:
    return hashlib.sha256(f"{version_id}:{index}:{text}".encode()).hexdigest()


def existing_chunks(db: Session, version_id: int) -> list[Chunk]:
    return list(
        db.execute(select(Chunk).where(Chunk.version_id == version_id).order_by(Chunk.chunk_index)).scalars()
    )


def index_version(db: Session, version_id: int, *, force: bool = False) -> dict:
    """Chunk a version, compute its embeddings, and persist both.

    Returns `{"chunks": n, "created": n, "embedded": n}`.
    """
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise ValueError(f"document version {version_id} does not exist")

    present = existing_chunks(db, version_id)
    if present and force:
        # Only `force=True` destroys chunks -- and only then do claims lose their
        # chunk link (claims survive via ON DELETE SET NULL).
        db.delete(present)
        db.flush()
        present = []

    if present:
        result = _fill_missing_embeddings(db, present)
        return {"chunks": len(present), "created": 0, "embedded": result}

    spans = chunk_spans(version.clean_text)
    if not spans:
        return {"chunks": 0, "created": 0, "embedded": 0}

    chunks = [
        Chunk(
            user_id=version.user_id,
            document_id=version.document_id,
            version_id=version.id,
            chunk_index=span.index,
            text=version.clean_text[span.start : span.end],
            char_start=span.start,
            char_end=span.end,
            token_estimate=span.tokens,
            chunk_hash=chunk_hash(version.clean_text[span.start : span.end], version.id, span.index),
            search_text=build_search_text(version.clean_text[span.start : span.end]),
            metadata_={},
        )
        for span in spans
    ]
    db.add_all(chunks)
    db.flush()

    embedded = _embed_chunks(db, chunks)
    db.flush()
    return {"chunks": len(chunks), "created": len(chunks), "embedded": embedded}


def _embed_chunks(db: Session, chunks: list[Chunk]) -> int:
    embedder = get_embedder()
    done = 0
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        pending = [c for c in batch if c.embedding is None]
        if not pending:
            continue
        vectors = embedder.embed_documents([c.text for c in pending])
        for chunk, vector in zip(pending, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = embedder.name
            done += 1
    return done


def _fill_missing_embeddings(db: Session, chunks: list[Chunk]) -> int:
    """Embed only the rows that need it.

    Rows written by a *different* embedder are recomputed too: mixing vector spaces
    in one HNSW index produces silently wrong neighbours.
    """
    embedder = get_embedder()
    stale = [c for c in chunks if c.embedding is None or c.embedding_model != embedder.name]
    _embed_chunks(db, stale)
    return len(stale)


def reindex_embeddings(db: Session, user_id=None, *, batch: int = 256) -> dict:
    """Recompute embeddings whose `embedding_model` differs from the current embedder."""
    embedder = get_embedder()
    stats = {"chunks": 0, "claims": 0, "model": embedder.name}
    for model in (Chunk,):
        _reindex_model(db, model, embedder, user_id, batch, stats)
    try:
        from app.models.claim import Claim  # noqa: PLC0415 - optional, arrives in M8

        _reindex_model(db, Claim, embedder, user_id, batch, stats)
    except ImportError:
        pass  # claims do not exist yet; M8 adds them
    return stats


def _reindex_model(db: Session, model, embedder, user_id, batch: int, stats: dict) -> None:
    key = "claims" if model.__name__ == "Claim" else "chunks"
    stmt = select(model).where((model.embedding.is_(None)) | (model.embedding_model != embedder.name))
    if user_id is not None:
        stmt = stmt.where(model.user_id == user_id)
    rows = list(db.execute(stmt.limit(batch)).scalars())
    for row in rows:
        text = getattr(row, "statement", None) or getattr(row, "text", "")
        row.embedding = embedder.embed_query(text)
        row.embedding_model = embedder.name
        stats[key] += 1
    if rows:
        db.flush()


def count_chunks(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Chunk)).scalar_one()


__all__ = ["count_chunks", "existing_chunks", "index_version", "reindex_embeddings"]
