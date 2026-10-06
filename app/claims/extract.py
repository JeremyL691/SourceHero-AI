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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.claims.align import align_quote
from app.claims.heuristic_extract import ClaimDraft, heuristic_claims
from app.core import time as clock
from app.core.logging import get_logger
from app.indexing.embeddings import get_embedder
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


def extract_version_claims(db: Session, version_id: int, *, extractor: str = "heuristic") -> ExtractStats:
    """Extract and persist every claim in one version. Idempotent."""
    stats = ExtractStats()
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise ValueError(f"document version {version_id} does not exist")

    chunks = list(
        db.execute(select(Chunk).where(Chunk.version_id == version_id).order_by(Chunk.chunk_index)).scalars()
    )
    stats.chunks = len(chunks)

    for chunk in chunks:
        drafts = _drafts_for(chunk.text, chunk.char_start, stats=stats)
        stats.drafts += len(drafts)
        for draft in drafts:
            stored = _persist(db, version, chunk, draft, extractor=extractor)
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


def _drafts_for(text: str, start: int, *, stats: ExtractStats | None = None) -> list[ClaimDraft]:
    return heuristic_claims(text, start, stats=stats)


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
