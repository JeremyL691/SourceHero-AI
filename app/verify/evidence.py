"""Evidence collection.

The verifier compares sentences, not chunks, so this narrows a chunk down to its most
relevant sentences and reports *absolute* offsets into the version's text -- the
reader highlights with those directly, and the claim ledger stores them as quotes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.claims.signals import token_f1
from app.ingestion.segment import split_sentences
from app.models.chunk import Chunk
from app.models.document import Document
from app.verify.config import EVIDENCE_SENTENCES_PER_CHUNK


@dataclass
class EvidenceSpan:
    """One sentence of source text, located precisely enough to cite."""

    chunk_id: int
    document_id: uuid.UUID
    version_id: int
    # Absolute offsets into `version.clean_text`, so the reader can highlight them.
    char_start: int
    char_end: int
    text: str
    title: str = ""
    url: str | None = None
    source_name: str = ""
    source_type: str = ""
    published_at: datetime | None = None
    score: float = 0.0
    page: int | None = None

    def citation(self, number: int) -> dict:
        """The JSON the UI renders as a citation chip."""
        return {
            "n": number,
            "chunk_id": self.chunk_id,
            "document_id": str(self.document_id),
            "version_id": self.version_id,
            "title": self.title,
            "url": self.url,
            "source": self.source_name,
            "date": self.published_at.isoformat() if self.published_at else None,
            "quote": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page": self.page,
            "score": round(self.score, 4),
        }


def gather_evidence(
    db: Session,
    user_id: uuid.UUID,
    sentence: str,
    *,
    k: int = 5,
    chunk_ids: list[int] | None = None,
) -> list[EvidenceSpan]:
    """Find the sentences most relevant to `sentence`.

    With no `chunk_ids`, retrieval picks the chunks; with them, only the cited chunks
    are considered -- which is what the Ask flow needs when a sentence already names
    its citations and retrieval would only dilute the comparison.
    """
    if chunk_ids:
        chunks = load_cited_chunks(db, user_id, chunk_ids)
    else:
        from app.retrieval.hybrid import hybrid_search

        result = hybrid_search(db, user_id, sentence, k=k)
        chunks = [hit.chunk_id for hit in result.hits]

    spans: list[EvidenceSpan] = []
    for chunk_id in chunks:
        spans.extend(_spans_from_chunk(db, chunk_id, sentence))
    spans.sort(key=lambda s: s.score, reverse=True)
    return spans


def load_cited_chunks(db: Session, user_id: uuid.UUID, chunk_ids: list[int]) -> list[int]:
    """The subset of `chunk_ids` this user owns.

    A cited chunk from an old version is still legitimate evidence of what was said
    then, so version currency is not filtered here; the caller decides. Anything
    belonging to another tenant is dropped rather than fetched.
    """
    if not chunk_ids:
        return []
    from sqlalchemy import select

    return list(
        db.execute(select(Chunk.id).where(Chunk.id.in_(chunk_ids), Chunk.user_id == user_id)).scalars()
    )


def chunk_evidence_spans(db: Session, chunk_id: int) -> list[EvidenceSpan]:
    """Every sentence of a chunk, with its absolute offsets.

    Callers that need a ranking score against a query go through `gather_evidence`;
    the Ask flow needs the cited chunk's sentences unscored, because the citation
    itself already says which chunk the model meant.
    """
    return _spans_from_chunk(db, chunk_id, "")


def _spans_from_chunk(db: Session, chunk_id: int, sentence: str) -> list[EvidenceSpan]:
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        return []

    document = db.get(Document, chunk.document_id) if chunk.document_id else None
    title = document.title if document else ""
    url = document.url if document else None
    published_at = document.published_at if document else None
    source_name = source_type = ""
    if document is not None:
        from app.models.source import Source

        source = db.get(Source, document.source_id)
        if source is not None:
            source_name = source.name
            source_type = source.source_type

    # The chunk text is a slice of the version, so sentence offsets inside the chunk
    # become absolute offsets by adding the chunk's start.
    candidates = []
    for span in split_sentences(chunk.text):
        text = span.text_of(chunk.text)
        score = token_f1(sentence, text)
        candidates.append(
            EvidenceSpan(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                version_id=chunk.version_id,
                char_start=chunk.char_start + span.start,
                char_end=chunk.char_start + span.end,
                text=text,
                title=title,
                url=url,
                source_name=source_name,
                source_type=source_type,
                published_at=published_at,
                page=chunk.page,
                score=score,
            )
        )

    candidates.sort(key=lambda s: s.score, reverse=True)
    return candidates[:EVIDENCE_SENTENCES_PER_CHUNK]


__all__ = ["EvidenceSpan", "chunk_evidence_spans", "gather_evidence", "load_cited_chunks"]
