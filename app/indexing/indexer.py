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


def _page_for_span(page_map: list[dict] | None, char_start: int) -> int | None:
    if not page_map:
        return None
    for entry in page_map:
        if entry.get("start", 0) <= char_start < entry.get("end", 0):
            return entry.get("page")
    for entry in page_map:
        if entry.get("start", 0) <= char_start <= entry.get("end", 0):
            return entry.get("page")
    return page_map[-1].get("page") if page_map else None


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
        for c in present:
            db.delete(c)
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
            page=_page_for_span(version.page_map, span.start),
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


def reindex_embeddings(
    db: Session,
    user_id=None,
    *,
    batch: int = 256,
    ctx=None,
) -> dict:
    """Recompute embeddings whose `embedding_model` differs from the current embedder."""
    embedder = get_embedder()
    stats = {"chunks": 0, "claims": 0, "model": embedder.name}
    for model in (Chunk,):
        _reindex_model(db, model, embedder, user_id, batch, stats, ctx=ctx)
    try:
        from app.models.claim import Claim  # noqa: PLC0415 - optional, arrives in M8

        _reindex_model(db, Claim, embedder, user_id, batch, stats, ctx=ctx)
    except ImportError:
        pass  # claims do not exist yet; M8 adds them

    _recompute_all_centroids(db, user_id, embedder)
    return stats


def _reindex_model(db: Session, model, embedder, user_id, batch: int, stats: dict, ctx=None) -> None:
    key = "claims" if model.__name__ == "Claim" else "chunks"
    while True:
        if hasattr(model, "embedding_model"):
            stmt = select(model).where((model.embedding.is_(None)) | (model.embedding_model != embedder.name))
        else:
            stmt = select(model).where(model.embedding.is_(None))
        if user_id is not None:
            stmt = stmt.where(model.user_id == user_id)
        rows = list(db.execute(stmt.limit(batch)).scalars().all())
        if not rows:
            break
        for row in rows:
            text = getattr(row, "statement", None) or getattr(row, "text", "")
            row.embedding = embedder.embed_query(text)
            if hasattr(row, "embedding_model"):
                row.embedding_model = embedder.name
            stats[key] += 1
        db.flush()
        if ctx is not None:
            ctx.check_cancelled()
            ctx.progress(stats)
        if len(rows) < batch:
            break


def _recompute_all_centroids(db: Session, user_id, embedder) -> None:
    import numpy as np

    try:
        from app.models.claim import Claim, ClaimCluster
        from app.models.temporal import Watch
        from app.models.wiki import Topic
    except ImportError:
        return

    # 1. Clusters
    cluster_stmt = select(ClaimCluster)
    if user_id is not None:
        cluster_stmt = cluster_stmt.where(ClaimCluster.user_id == user_id)
    clusters = list(db.execute(cluster_stmt).scalars().all())
    for cluster in clusters:
        members = list(
            db.execute(select(Claim).where(Claim.cluster_id == cluster.id, Claim.status == "active"))
            .scalars()
            .all()
        )
        cluster.size = len(members)
        cluster.doc_count = len({m.document_id for m in members})
        vecs = [list(m.embedding) for m in members if m.embedding is not None]
        if vecs:
            stacked = np.array(vecs)
            norm = np.linalg.norm(stacked.mean(axis=0))
            cluster.centroid = (
                (stacked.mean(axis=0) / norm).tolist() if norm > 0 else stacked.mean(axis=0).tolist()
            )
        else:
            cluster.centroid = None

    # 2. Topics
    topic_stmt = select(Topic)
    if user_id is not None:
        topic_stmt = topic_stmt.where(Topic.user_id == user_id)
    topics = list(db.execute(topic_stmt).scalars().all())
    for topic in topics:
        topic_clusters = list(
            db.execute(
                select(ClaimCluster).where(
                    ClaimCluster.topic_id == topic.id, ClaimCluster.centroid.is_not(None)
                )
            )
            .scalars()
            .all()
        )
        vecs = [list(c.centroid) for c in topic_clusters if c.centroid is not None]
        if vecs:
            stacked = np.array(vecs)
            norm = np.linalg.norm(stacked.mean(axis=0))
            topic.centroid = (
                (stacked.mean(axis=0) / norm).tolist() if norm > 0 else stacked.mean(axis=0).tolist()
            )
        else:
            topic.centroid = None

    # 3. Query Watches
    watch_stmt = select(Watch).where(Watch.kind == "query")
    if user_id is not None:
        watch_stmt = watch_stmt.where(Watch.user_id == user_id)
    watches = list(db.execute(watch_stmt).scalars().all())
    for watch in watches:
        if watch.query:
            watch.embedding = embedder.embed_query(watch.query)

    db.flush()


def count_chunks(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Chunk)).scalar_one()


__all__ = ["count_chunks", "existing_chunks", "index_version", "reindex_embeddings"]
