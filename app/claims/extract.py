"""Claim extraction: turn a version's chunks into ledger rows.

The invariant this module exists to guarantee:

    version.clean_text[claim.char_start:claim.char_end] == claim.quote

It holds because the quote is *re-read from the source text* after alignment, never
taken from whatever produced it. A claim whose quote cannot be located in the source is
dropped and counted, not stored.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.claims.align import align_quote
from app.claims.heuristic_extract import ClaimDraft, heuristic_claims
from app.core import time as clock
from app.core.logging import get_logger
from app.indexing.embeddings import get_embedder
from app.llm.providers.fake import register_default
from app.models.chunk import Chunk
from app.models.claim import Claim
from app.models.document import DocumentVersion

log = get_logger(__name__)

_NORMALIZE = re.compile(r"\s+")


def claim_hash(statement: str, char_start: int, char_end: int) -> str:
    """Identity of a claim within a version.

    Offsets are part of the key, so the same sentence appearing twice in one document
    yields two claims rather than colliding into one.
    """
    normalized = _NORMALIZE.sub(" ", statement).strip().lower()
    return hashlib.sha256(f"{normalized}|{char_start}|{char_end}".encode()).hexdigest()


def build_claim_extract_context(
    title: str,
    passage: str,
    published_at: str | None = None,
) -> dict:
    return {
        "title": title,
        "passage": passage,
        "published_at": published_at,
    }


@dataclass
class ExtractStats:
    chunks: int = 0
    drafts: int = 0
    created: int = 0
    unchanged: int = 0
    rejected_unaligned: int = 0
    rejected_over_limit: int = 0
    ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "chunks": self.chunks,
            "drafts": self.drafts,
            "created": self.created,
            "unchanged": self.unchanged,
            "rejected_unaligned": self.rejected_unaligned,
            "rejected_over_limit": self.rejected_over_limit,
        }


@register_default("claim_extract")
def _fake_claim_extract(context: dict) -> dict:
    passage = context.get("passage", "")
    chunk_start = context.get("chunk_start", 0)
    drafts = heuristic_claims(passage, chunk_start)
    claims = []
    for d in drafts:
        claims.append(
            {
                "statement": d.statement,
                "quote": d.quote,
                "subject": d.subject,
                "predicate": d.predicate,
                "object": d.object,
                "qualifiers": d.qualifiers,
                "claim_type": d.claim_type
                if d.claim_type in ("fact", "number", "date", "prediction", "opinion")
                else "fact",
                "numbers": [
                    {
                        "value": float(n.get("value", 0.0)),
                        "unit": str(n.get("unit", "")),
                        "text": str(n.get("text", "")),
                    }
                    for n in (d.numbers or [])
                ],
                "confidence": d.confidence,
            }
        )
    return {"claims": claims}


def extract_version_claims(db: Session, version_id: int, *, extractor: str = "auto") -> ExtractStats:
    """Extract and persist every claim in one version. Idempotent."""
    stats = ExtractStats()
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise ValueError(f"document version {version_id} does not exist")

    from app.llm.client import get_llm
    from app.models.document import Document

    document = db.get(Document, version.document_id)
    doc_title = document.title if document and document.title else f"doc_{version.document_id}"
    pub_date = version.effective_at.date().isoformat() if version.effective_at else None

    client = None
    if extractor != "heuristic":
        client = get_llm(db=db, user_id=version.user_id)

    chunks = list(
        db.execute(select(Chunk).where(Chunk.version_id == version_id).order_by(Chunk.chunk_index)).scalars()
    )
    stats.chunks = len(chunks)

    for chunk in chunks:
        drafts, method = _drafts_for(
            chunk.text,
            chunk.char_start,
            title=doc_title,
            published_at=pub_date,
            client=client,
            user_id=version.user_id,
            stats=stats,
        )
        stats.drafts += len(drafts)
        for draft in drafts:
            stored = _persist(db, version, chunk, draft, extractor=method)
            if stored is None:
                stats.rejected_unaligned += 1
                continue
            if stored:
                stats.created += 1
                stats.ids.append(stored.id)
            else:
                stats.unchanged += 1

    db.flush()
    return stats


def _drafts_for(
    text: str,
    start: int,
    *,
    title: str = "",
    published_at: str | None = None,
    client: Any = None,
    user_id: uuid.UUID | None = None,
    stats: ExtractStats | None = None,
) -> tuple[list[ClaimDraft], str]:
    if client is not None and client.effective.provider != "heuristic":
        try:
            from app.llm.prompts import render
            from app.llm.schemas import ClaimExtraction
            from app.llm.types import LLMRequest

            ctx = build_claim_extract_context(title=title, passage=text, published_at=published_at)
            messages, version = render("claim_extract", schema=ClaimExtraction, **ctx)
            result = client.complete(
                LLMRequest(
                    task="claim_extract",
                    messages=messages,
                    tier="fast",
                    schema=ClaimExtraction,
                    prompt_version=version,
                    user_id=user_id,
                    context={**ctx, "chunk_start": start},
                )
            )
            if result.parsed is not None and isinstance(result.parsed, ClaimExtraction):
                drafts: list[ClaimDraft] = []
                for item in result.parsed.claims:
                    drafts.append(
                        ClaimDraft(
                            statement=item.statement,
                            quote=item.quote,
                            char_start=start,
                            char_end=start + len(text),
                            claim_type=item.claim_type,
                            subject=item.subject or "",
                            predicate=item.predicate or "",
                            object=item.object or "",
                            qualifiers=item.qualifiers or {},
                            numbers=[{"value": n.value, "unit": n.unit, "text": n.text} for n in item.numbers]
                            if item.numbers
                            else [],
                            polarity=1,
                            confidence=item.confidence or 0.6,
                        )
                    )
                return drafts, f"llm({client.effective.provider})"
        except Exception as exc:
            log.warning("LLM claim extraction failed, falling back to heuristic: %s", exc)

    return heuristic_claims(text, start, stats=stats), "heuristic"


def _persist(
    db: Session, version: DocumentVersion, chunk: Chunk, draft: ClaimDraft, *, extractor: str
) -> Claim | None | bool:
    """Store one draft.

    Returns the created Claim, None when the quote could not be aligned (rejected), or
    False when an identical claim already exists (unchanged).
    """
    span = align_quote(draft.quote, chunk.text, chunk.char_start)
    if span is None:
        span = align_quote(draft.quote, version.clean_text, 0)
    if span is None:
        # Unlocatable quote: the ledger refuses to store a claim it cannot check.
        return None
    start, end = span

    digest = claim_hash(draft.statement, start, end)
    existing = db.execute(
        select(Claim).where(
            Claim.user_id == version.user_id,
            Claim.version_id == version.id,
            Claim.claim_hash == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    # The quote is re-read from the source, so the invariant holds by construction.
    quote = version.clean_text[start:end]

    claim = Claim(
        user_id=version.user_id,
        document_id=version.document_id,
        version_id=version.id,
        chunk_id=chunk.id,
        statement=draft.statement,
        quote=quote,
        char_start=start,
        char_end=end,
        subject=draft.subject,
        predicate=draft.predicate,
        object=draft.object,
        qualifiers=draft.qualifiers,
        claim_type=draft.claim_type,
        numbers=draft.numbers,
        polarity=draft.polarity,
        # World time comes from the *version*, not the document: a new version's
        # claims must not inherit the original publication date, or the temporal
        # reconcile would immediately supersede them.
        asserted_at=version.effective_at,
        observed_at=clock.utcnow(),
        status="active",
        claim_hash=digest,
        extractor=extractor,
        prompt_version=None,
        confidence=draft.confidence,
    )
    if chunk.text:
        claim.embedding = get_embedder().embed_query(draft.statement)

    try:
        with db.begin_nested():
            db.add(claim)
            db.flush()
    except IntegrityError:
        # Another run inserted the same claim concurrently.
        return False

    from app.claims.relate import record_assertion

    record_assertion(db, claim, at=claim.asserted_at)
    return claim


def claims_for_chunks(db: Session, chunk_ids: list[int]) -> dict[int, list[Claim]]:
    """Claims grouped by chunk -- what the Ask flow uses to flag related disputes."""
    if not chunk_ids:
        return {}
    rows = db.execute(select(Claim).where(Claim.chunk_id.in_(chunk_ids), Claim.status == "active")).scalars()
    grouped: dict[int, list[Claim]] = {}
    for claim in rows:
        grouped.setdefault(claim.chunk_id, []).append(claim)
    return grouped


def verify_quote_invariant(db: Session, user_id: uuid.UUID | None = None) -> list[tuple[int, int]]:
    """Every claim whose stored offsets do not reproduce its quote.

    Returns the offending `(claim_id, version_id)` pairs; an empty list is the pass
    condition, and `tests/test_claims.py` asserts it after seeding the demo corpus.
    """
    stmt = select(Claim.id, Claim.version_id, Claim.char_start, Claim.char_end, Claim.quote)
    if user_id is not None:
        stmt = stmt.where(Claim.user_id == user_id)
    rows = db.execute(stmt).all()

    versions: dict[int, str] = {}
    bad: list[tuple[int, int]] = []
    for claim_id, version_id, start, end, quote in rows:
        if version_id not in versions:
            version = db.get(DocumentVersion, version_id)
            versions[version_id] = version.clean_text if version else ""
        text = versions[version_id]
        if text[start:end] != quote:
            bad.append((claim_id, version_id))
    return bad


__all__ = [
    "ExtractStats",
    "claim_hash",
    "claims_for_chunks",
    "extract_version_claims",
    "verify_quote_invariant",
]
