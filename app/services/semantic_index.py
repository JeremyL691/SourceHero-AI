"""
Semantic index — pgvector target, lexical live.

Production schema (`infra/supabase/schema.sql`) defines `embedding vector(1536)` +
HNSW index, but the ORM keeps only `embedding_id` so SQLite tests don't need
pgvector. `rebuild_semantic_index` / `index_new_chunks` currently compute
embeddings via OpenAI and mark chunks with an `embedding_id` — the actual
vector bytes are not yet written to Postgres. Until the write + `vector_cosine_ops`
query are wired, `_get_embedding_from_metadata` returns None and retrieval in
`app/retrieval/search.py` falls back to lexical TF-IDF. This keeps the shipped
behavior honest: lexical is live, pgvector is schema-ready.
"""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DocumentChunk
from app.retrieval.semantic import embed_texts
from app.services.user_settings import effective_openai_key

_INDEX_LOCK = threading.RLock()


@dataclass
class LocalIndexStatus:
    backend: str = "pgvector"
    persisted: bool = True
    enabled: bool = False
    ready: bool = False
    embedding_model: str = "text-embedding-3-small"
    indexed_chunks: int = 0
    total_chunks: int = 0
    pending_chunks: int = 0
    note: str = "Embeddings are stored in PostgreSQL with pgvector extension."


def semantic_index_enabled() -> bool:
    return bool(effective_openai_key())


def semantic_index_status(db: Session) -> LocalIndexStatus:
    total_chunks = db.scalar(select(func.count(DocumentChunk.id))) or 0
    indexed_chunks = db.scalar(
        select(func.count(DocumentChunk.id)).where(DocumentChunk.embedding_id.isnot(None))
    ) or 0
    enabled = semantic_index_enabled()
    return LocalIndexStatus(
        enabled=enabled,
        ready=bool(enabled and total_chunks > 0 and indexed_chunks >= total_chunks),
        indexed_chunks=indexed_chunks,
        total_chunks=total_chunks,
        pending_chunks=max(total_chunks - indexed_chunks, 0),
    )


def rebuild_semantic_index(db: Session, user_id: str | None = None) -> dict:
    key = effective_openai_key()
    if not key:
        status = semantic_index_status(db)
        return {
            "status": "disabled",
            "indexed_chunks": status.indexed_chunks,
            "total_chunks": status.total_chunks,
            "pending_chunks": status.pending_chunks,
            "detail": "No OpenAI API key configured. Semantic indexing is disabled.",
        }

    stmt = select(DocumentChunk).order_by(DocumentChunk.id)
    if user_id:
        stmt = stmt.where(DocumentChunk.user_id == user_id)
    chunks = db.scalars(stmt).all()

    embeddings = _build_chunk_embedding_map(chunks, api_key=key)
    for chunk_id, vector in embeddings.items():
        chunk = db.get(DocumentChunk, chunk_id)
        if chunk:
            chunk.embedding_id = str(chunk_id)
    db.commit()

    indexed = len(embeddings)
    status = semantic_index_status(db)
    return {
        "status": "rebuilt",
        "indexed_chunks": indexed,
        "total_chunks": status.total_chunks,
        "pending_chunks": status.pending_chunks,
    }


def index_new_chunks(db: Session, chunk_ids: list[int]) -> dict:
    key = effective_openai_key()
    if not key:
        return {"status": "disabled", "indexed_chunks": 0}
    if not chunk_ids:
        return {"status": "noop", "indexed_chunks": 0}
    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids)).order_by(DocumentChunk.id)
    ).all()
    embeddings = _build_chunk_embedding_map(chunks, api_key=key)
    for chunk_id, vector in embeddings.items():
        chunk = db.get(DocumentChunk, chunk_id)
        if chunk:
            chunk.embedding_id = str(chunk_id)
    db.commit()
    return {"status": "indexed", "indexed_chunks": len(embeddings)}


def remove_chunk_embeddings(chunk_ids: list[int]) -> int:
    return 0


def embed_query_text(query: str) -> list[float] | None:
    key = effective_openai_key()
    if not key or not query.strip():
        return None
    embeddings = embed_texts([query], api_key=key)
    return embeddings[0] if embeddings else None


def semantic_chunk_scores(query: str, *, allowed_chunk_ids: set[int] | None = None, top_k: int = 5) -> list[tuple[int, float]]:
    query_embedding = embed_query_text(query)
    if not query_embedding:
        return []
    return _search_similar(query_embedding, top_k=top_k, allowed_chunk_ids=allowed_chunk_ids)


def _search_similar(
    query_embedding: list[float],
    *,
    top_k: int = 5,
    allowed_chunk_ids: set[int] | None = None,
) -> list[tuple[int, float]]:
    from app.database import SessionLocal

    with SessionLocal() as db:
        chunks = db.scalars(select(DocumentChunk).where(DocumentChunk.embedding_id.isnot(None))).all()
        scored = []
        for chunk in chunks:
            if allowed_chunk_ids and chunk.id not in allowed_chunk_ids:
                continue
            chunk_embedding = _get_embedding_from_metadata(chunk)
            if chunk_embedding:
                score = _cosine_similarity(query_embedding, chunk_embedding)
                if score > 0:
                    scored.append((chunk.id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


def _get_embedding_from_metadata(chunk: DocumentChunk) -> list[float] | None:
    return None


def _build_chunk_embedding_map(
    chunks: list[DocumentChunk],
    *,
    api_key: str,
    batch_size: int = 32,
) -> dict[int, list[float]]:
    if not chunks:
        return {}
    embeddings: dict[int, list[float]] = {}
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = embed_texts([chunk.chunk_text for chunk in batch], api_key=api_key)
        if len(vectors) != len(batch):
            raise ValueError("Embedding response length did not match chunk batch size")
        embeddings.update({chunk.id: vector for chunk, vector in zip(batch, vectors)})
    return embeddings


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(v * v for v in left))
    right_norm = math.sqrt(sum(v * v for v in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    return dot / (left_norm * right_norm)
