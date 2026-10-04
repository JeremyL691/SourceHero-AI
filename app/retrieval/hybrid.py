"""Hybrid retrieval: Postgres full-text search fused with pgvector kNN by RRF.

Two design decisions carry most of the weight here.

*Ranking fusion, not score blending.* Lexical `ts_rank_cd` and vector cosine distance
are not comparable numbers -- one is unbounded and the other a distance. Reciprocal
rank fusion only looks at each list's ordering, so both legs contribute on their own
scale and no normalization constant has to be invented.

*Bound the vector parameters.* psycopg3 sends a Python list as `numeric[]`, which makes
`<=>` fail. The query therefore goes through the ORM's `cosine_distance`, which binds
as `vector`.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.text import snippet as make_snippet
from app.indexing.embeddings import get_embedder
from app.indexing.tokenize import build_tsquery, search_tokens
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.library import CollectionItem, ItemTag
from app.models.source import Source
from app.retrieval.types import Hit, SearchFilters, SearchResult

log = get_logger(__name__)

SNIPPET_RADIUS = 150

# Set once per process: pgvector >= 0.8 can relax HNSW's ordering under filters,
# which matters because every query filters by user_id and would otherwise silently
# return fewer rows than it should.
_iterative_scan_supported: bool | None = None


def _pgvector_version(db: Session) -> str:
    global _iterative_scan_supported
    if _iterative_scan_supported is None:
        try:
            version = db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
            parts = tuple(int(p) for p in str(version).split(".")[:2] if p.isdigit())
            _iterative_scan_supported = parts >= (0, 8)
        except Exception:  # noqa: BLE001 - a missing extension is reported by /readyz
            _iterative_scan_supported = False
    return "relaxed_order" if _iterative_scan_supported else "strict_order"


def reset_pgvector_probe() -> None:
    global _iterative_scan_supported
    _iterative_scan_supported = None


# --- filters -------------------------------------------------------------


def _apply_filters(stmt: Select, filters: SearchFilters | None) -> Select:
    """Apply every narrowing constraint.

    The current-version restriction is a *default*, not an opt-in: it applies even
    when no filter object is supplied. Skipping it when `filters is None` would let a
    plain `/v1/search` return a superseded version and present it as present-tense
    fact -- the one thing the temporal model exists to prevent.
    """
    if filters is None:
        return stmt.where(Chunk.version_id == Document.current_version_id)

    if not filters.include_old_versions:
        stmt = stmt.where(Chunk.version_id == Document.current_version_id)

    if filters.source_ids:
        stmt = stmt.where(Document.source_id.in_(filters.source_ids))
    if filters.source_types:
        stmt = stmt.where(Source.source_type.in_(filters.source_types))
    if filters.date_from:
        stmt = stmt.where(Document.published_at >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(Document.published_at <= filters.date_to)
    if filters.collection_id is not None:
        stmt = stmt.where(
            Chunk.document_id.in_(
                select(CollectionItem.document_id).where(
                    CollectionItem.collection_id == filters.collection_id
                )
            )
        )
    if filters.tag_ids:
        stmt = stmt.where(
            Chunk.document_id.in_(select(ItemTag.document_id).where(ItemTag.tag_id.in_(filters.tag_ids)))
        )
    return stmt


def _base_select(user_id: uuid.UUID):
    """The shared FROM/JOIN so both legs rank over identical candidate rows."""
    return (
        select(
            Chunk.id.label("chunk_id"),
            Chunk.document_id,
            Chunk.version_id,
            Chunk.text,
            Chunk.char_start,
            Chunk.char_end,
            Chunk.page,
            Document.title,
            Document.url,
            Document.published_at,
            Document.current_version_id,
            Source.id.label("source_id"),
            Source.name.label("source_name"),
            Source.source_type,
        )
        .join(Document, Document.id == Chunk.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(Chunk.user_id == user_id)
    )


def _row_to_hit(row, scores: dict, query: str = "") -> Hit:
    text_ = row.text
    return Hit(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        version_id=row.version_id,
        title=row.title or "",
        url=row.url,
        source_id=row.source_id,
        source_name=row.source_name or "",
        source_type=row.source_type or "",
        published_at=row.published_at,
        text=text_,
        char_start=row.char_start,
        char_end=row.char_end,
        page=row.page,
        snippet=make_snippet(text_, _first_hit_offset(text_, query), SNIPPET_RADIUS),
        scores=scores,
    )


def _first_hit_offset(text: str, query: str) -> int:
    """Where the query's first token occurs, so the snippet centres on the match.

    A snippet taken from the start of a 300-token chunk usually shows the wrong
    sentence; the reader highlights by offset anyway, so this only affects what the
    reader sees before clicking.
    """
    lowered = text.lower()
    for token in search_tokens(query):
        found = lowered.find(token)
        if found >= 0:
            return found
    return 0


# --- the two legs --------------------------------------------------------


def _lexical_leg(
    db: Session, user_id: uuid.UUID, query: str, limit: int, filters: SearchFilters | None
) -> list[dict]:
    tsquery = build_tsquery(query)
    if tsquery is None:
        return []
    stmt = (
        _base_select(user_id)
        .add_columns(func.ts_rank_cd(Chunk.tsv, func.to_tsquery("simple", tsquery), 32).label("rank"))
        .where(Chunk.tsv.op("@@")(func.to_tsquery("simple", tsquery)))
    )
    stmt = _apply_filters(stmt, filters)
    stmt = stmt.order_by(text("rank DESC"), Chunk.id).limit(limit)
    return [{"row": row, "score": float(row.rank)} for row in db.execute(stmt).all()]


def _vector_leg(
    db: Session,
    user_id: uuid.UUID,
    query: str,
    limit: int,
    filters: SearchFilters | None,
    embedder_name: str,
    vector: list[float],
) -> list[dict]:
    stmt = (
        _base_select(user_id)
        # ORM expression, not raw `<=> :qvec`: psycopg3 would send a Python list as
        # numeric[] and the operator would fail to resolve.
        .add_columns(Chunk.embedding.cosine_distance(vector).label("distance"))
        .where(Chunk.embedding.is_not(None))
        .where(Chunk.embedding_model == embedder_name)
    )
    stmt = _apply_filters(stmt, filters)
    stmt = stmt.order_by(Chunk.embedding.cosine_distance(vector)).limit(limit)
    return [{"row": row, "score": 1.0 - float(row.distance)} for row in db.execute(stmt).all()]


# --- fusion --------------------------------------------------------------


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[int, float]]], k: int | None = None
) -> dict[int, float]:
    """RRF: `sum over lists of 1 / (k + rank)`, ranks 1-based.

    A document found by both legs beats one found by either alone, without needing
    the two scores to be on a common scale.
    """
    k = k or get_settings().rrf_k
    fused: dict[int, float] = {}
    for ranked in ranked_lists:
        for position, (chunk_id, _score) in enumerate(ranked, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + position)
    return fused


def hybrid_search(
    db: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    k: int = 8,
    mode: str = "hybrid",
    rerank: bool | None = None,
    filters: SearchFilters | None = None,
) -> SearchResult:
    """Search a user's current-version chunks.

    Falls back to whichever leg produced results when the other found nothing, so a
    query with no lexical overlap is still answerable by the vector leg and vice
    versa. `mode_effective` reports which path actually ran.
    """
    settings = get_settings()
    candidates = settings.retrieval_candidates
    timings: dict[str, float] = {}
    started = time.perf_counter()

    embedder = get_embedder()
    want_lexical = mode in ("hybrid", "lexical")
    want_vector = mode in ("hybrid", "vector")

    lexical: list[dict] = []
    if want_lexical:
        t0 = time.perf_counter()
        lexical = _lexical_leg(db, user_id, query, candidates, filters)
        timings["lexical"] = (time.perf_counter() - t0) * 1000

    vector: list[dict] = []
    if want_vector and search_tokens(query):
        t0 = time.perf_counter()
        db.execute(text("SET LOCAL hnsw.ef_search = 100"))
        db.execute(text(f"SET LOCAL hnsw.iterative_scan = {_pgvector_version(db)}"))
        vector = _vector_leg(
            db,
            user_id,
            query,
            candidates,
            filters,
            embedder.name,
            embedder.embed_query(query),
        )
        timings["vector"] = (time.perf_counter() - t0) * 1000

    if not lexical and not vector:
        timings["total"] = (time.perf_counter() - started) * 1000
        return SearchResult([], "empty", timings, 0)

    by_id: dict[int, dict] = {}
    for entry in lexical + vector:
        by_id.setdefault(entry["row"].chunk_id, entry)

    if mode == "lexical" and lexical:
        fused = reciprocal_rank_fusion([[(e["row"].chunk_id, e["score"]) for e in lexical]])
    elif mode == "vector" and vector:
        fused = reciprocal_rank_fusion([[(e["row"].chunk_id, e["score"]) for e in vector]])
    elif lexical and vector:
        fused = reciprocal_rank_fusion(
            [
                [(e["row"].chunk_id, e["score"]) for e in lexical],
                [(e["row"].chunk_id, e["score"]) for e in vector],
            ]
        )
    else:
        fused = reciprocal_rank_fusion([[(e["row"].chunk_id, e["score"]) for e in (lexical or vector)]])

    lexical_rank = {e["row"].chunk_id: i for i, e in enumerate(lexical, start=1)}
    vector_rank = {e["row"].chunk_id: i for i, e in enumerate(vector, start=1)}

    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:candidates]
    hits = [
        _row_to_hit(
            by_id[chunk_id]["row"],
            {
                "lexical_rank": lexical_rank.get(chunk_id),
                "vector_rank": vector_rank.get(chunk_id),
                "rrf": round(score, 6),
                "rerank": None,
            },
            query,
        )
        for chunk_id, score in ordered
    ]

    mode_effective = mode
    if not lexical:
        mode_effective = "vector"
    elif not vector:
        mode_effective = "lexical"

    if rerank or (rerank is None and settings.rerank_provider != "none"):
        hits = _apply_rerank(query, hits, candidates, timings)

    timings["total"] = (time.perf_counter() - started) * 1000
    return SearchResult(hits[:k], mode_effective, timings, len(by_id))


def _apply_rerank(query: str, hits: list[Hit], pool: int, timings: dict[str, float]) -> list[Hit]:
    from app.retrieval.rerank import rerank

    if not hits:
        return hits
    t0 = time.perf_counter()
    scores = rerank(query, [h.text for h in hits[:pool]])
    timings["rerank"] = (time.perf_counter() - t0) * 1000
    if not scores:
        return hits
    for hit, score in zip(hits, scores, strict=False):
        hit.scores["rerank"] = round(float(score), 6)
    hits.sort(key=lambda h: -(h.scores.get("rerank") or -1.0))
    return hits


__all__ = [
    "SNIPPET_RADIUS",
    "hybrid_search",
    "reciprocal_rank_fusion",
    "reset_pgvector_probe",
]
