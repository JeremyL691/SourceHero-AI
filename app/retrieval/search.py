from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.chunking import clean_text, tokenize
from app.models import Document, DocumentChunk, Source
from app.services.semantic_index import semantic_chunk_scores, semantic_index_enabled, semantic_index_status
from app.services.library import document_filter_ids


@dataclass
class SearchResult:
    chunk_id: int
    document_id: int
    source_id: int
    title: str
    source_name: str
    source_type: str
    url: str | None
    local_path: str | None
    score: float
    snippet: str
    chunk_text: str
    metadata: dict


@dataclass
class RetrievalBundle:
    hits: list[SearchResult]
    effective_retrieval_mode: str


def search_documents(
    db: Session,
    user_id: str,
    query: str,
    top_k: int = 5,
    source_ids: list[int] | None = None,
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = None,
    retrieval_mode: str = "hybrid",
) -> list[SearchResult]:
    return retrieve_documents(
        db,
        user_id=user_id,
        query=query,
        top_k=top_k,
        source_ids=source_ids,
        source_type=source_type,
        collection_id=collection_id,
        tags=tags,
        retrieval_mode=retrieval_mode,
    ).hits


def retrieve_documents(
    db: Session,
    user_id: str,
    query: str,
    top_k: int = 5,
    source_ids: list[int] | None = None,
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = None,
    retrieval_mode: str = "hybrid",
) -> RetrievalBundle:
    query = query.strip()
    if not query:
        return RetrievalBundle(hits=[], effective_retrieval_mode="lexical")

    allowed_doc_ids = document_filter_ids(db, user_id, source_ids, source_type, collection_id, tags)
    if allowed_doc_ids is not None and not allowed_doc_ids:
        return RetrievalBundle(hits=[], effective_retrieval_mode=_effective_mode(db, retrieval_mode))

    effective_mode = _effective_mode(db, retrieval_mode)
    rows = _candidate_rows(db, user_id, allowed_doc_ids)
    if not rows:
        return RetrievalBundle(hits=[], effective_retrieval_mode=effective_mode)

    lexical_hits = _lexical_hits(rows, query)
    semantic_hits = _semantic_hits(rows, query, top_k=top_k) if effective_mode != "lexical" else []

    if effective_mode == "lexical":
        return RetrievalBundle(hits=lexical_hits[:top_k], effective_retrieval_mode=effective_mode)
    if effective_mode == "semantic":
        return RetrievalBundle(hits=semantic_hits[:top_k], effective_retrieval_mode=effective_mode)
    return RetrievalBundle(
        hits=_merge_hits(lexical_hits, semantic_hits, top_k=top_k),
        effective_retrieval_mode=effective_mode,
    )


def _candidate_rows(
    db: Session,
    user_id: str,
    allowed_doc_ids: set[int] | None,
) -> list[tuple[DocumentChunk, Document, Source]]:
    stmt = (
        select(DocumentChunk, Document, Source)
        .join(Document, DocumentChunk.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .where(DocumentChunk.user_id == user_id)
    )
    rows = db.execute(stmt).all()
    if allowed_doc_ids is not None:
        rows = [row for row in rows if row[1].id in allowed_doc_ids]
    return rows


def _lexical_hits(rows: list[tuple[DocumentChunk, Document, Source]], query: str) -> list[SearchResult]:
    query_terms = tokenize(query)
    if not query_terms:
        return []
    chunk_terms = [(row, Counter(tokenize(row[0].chunk_text))) for row in rows]
    doc_freq: Counter[str] = Counter()
    for _, terms in chunk_terms:
        doc_freq.update(terms.keys())

    query_counts = Counter(query_terms)
    total_docs = len(chunk_terms)
    scored: list[SearchResult] = []
    for (chunk, document, source), terms in chunk_terms:
        if not terms:
            continue
        score = 0.0
        for term, q_count in query_counts.items():
            if term not in terms:
                continue
            idf = math.log((1 + total_docs) / (1 + doc_freq[term])) + 1
            score += q_count * terms[term] * idf
        if score <= 0:
            continue
        score = score / math.sqrt(sum(v * v for v in terms.values()))
        metadata = json.loads(chunk.metadata_json or "{}")
        scored.append(
            SearchResult(
                chunk_id=chunk.id,
                document_id=document.id,
                source_id=source.id,
                title=document.title,
                source_name=source.name,
                source_type=source.source_type,
                url=document.url or source.url,
                local_path=metadata.get("local_path") or source.local_path,
                score=round(score, 4),
                snippet=_snippet(chunk.chunk_text, query_terms),
                chunk_text=chunk.chunk_text,
                metadata=metadata,
            )
        )

    return sorted(scored, key=lambda item: item.score, reverse=True)


def _semantic_hits(rows: list[tuple[DocumentChunk, Document, Source]], query: str, *, top_k: int) -> list[SearchResult]:
    by_chunk_id = {chunk.id: (chunk, document, source) for chunk, document, source in rows}
    allowed_chunk_ids = set(by_chunk_id)
    scored = semantic_chunk_scores(query, allowed_chunk_ids=allowed_chunk_ids, top_k=max(top_k * 4, top_k))
    hits: list[SearchResult] = []
    for chunk_id, score in scored:
        if chunk_id not in by_chunk_id:
            continue
        chunk, document, source = by_chunk_id[chunk_id]
        metadata = json.loads(chunk.metadata_json or "{}")
        hits.append(
            SearchResult(
                chunk_id=chunk.id,
                document_id=document.id,
                source_id=source.id,
                title=document.title,
                source_name=source.name,
                source_type=source.source_type,
                url=document.url or source.url,
                local_path=metadata.get("local_path") or source.local_path,
                score=round(score, 4),
                snippet=_snippet(chunk.chunk_text, tokenize(query)),
                chunk_text=chunk.chunk_text,
                metadata=metadata,
            )
        )
    return hits


def _merge_hits(lexical_hits: list[SearchResult], semantic_hits: list[SearchResult], *, top_k: int) -> list[SearchResult]:
    lexical_by_id = {hit.chunk_id: hit for hit in lexical_hits}
    semantic_by_id = {hit.chunk_id: hit for hit in semantic_hits}
    max_lexical = max((hit.score for hit in lexical_hits), default=0.0)
    max_semantic = max((hit.score for hit in semantic_hits), default=0.0)
    merged: list[SearchResult] = []

    for chunk_id in lexical_by_id.keys() | semantic_by_id.keys():
        lexical_hit = lexical_by_id.get(chunk_id)
        semantic_hit = semantic_by_id.get(chunk_id)
        base_hit = semantic_hit or lexical_hit
        if not base_hit:
            continue
        lexical_score = (lexical_hit.score / max_lexical) if lexical_hit and max_lexical > 0 else 0.0
        semantic_score = (semantic_hit.score / max_semantic) if semantic_hit and max_semantic > 0 else 0.0
        combined = (0.45 * lexical_score) + (0.55 * semantic_score)
        merged.append(
            SearchResult(
                chunk_id=base_hit.chunk_id,
                document_id=base_hit.document_id,
                source_id=base_hit.source_id,
                title=base_hit.title,
                source_name=base_hit.source_name,
                source_type=base_hit.source_type,
                url=base_hit.url,
                local_path=base_hit.local_path,
                score=round(combined, 4),
                snippet=base_hit.snippet,
                chunk_text=base_hit.chunk_text,
                metadata=base_hit.metadata,
            )
        )
    return sorted(merged, key=lambda item: item.score, reverse=True)[:top_k]


def _effective_mode(db: Session, requested_mode: str) -> str:
    if requested_mode == "lexical":
        return "lexical"
    status = semantic_index_status(db)
    semantic_ready = semantic_index_enabled() and status.ready
    if requested_mode == "semantic":
        return "semantic" if semantic_ready else "lexical"
    return "hybrid" if semantic_ready else "lexical"


def _snippet(text: str, query_terms: list[str], max_chars: int = 360) -> str:
    cleaned = clean_text(text)
    lower = cleaned.lower()
    positions = [lower.find(term.lower()) for term in query_terms if lower.find(term.lower()) >= 0]
    start = max(0, min(positions) - 90) if positions else 0
    snippet = cleaned[start : start + max_chars]
    prefix = "..." if start > 0 else ""
    suffix = "..." if start + max_chars < len(cleaned) else ""
    return f"{prefix}{snippet}{suffix}"
