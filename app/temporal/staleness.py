"""Stale notes: the user's own writing that newer sources have overtaken.

Two rules, both about the *same* situation from opposite directions:

1. The note agrees with something that has since been superseded. The note itself has
   not changed, but what it relied on no longer holds.
2. The note is contradicted or superseded by a newer non-note source.

Neither rule edits the note. The lifecycle deliberately leaves `note`/`clip` claims `active` even
when contradicted, because silently rewriting a user's own note is not this system's
call to make. Flagging it is.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.cluster import neighbours_of_claim, thresholds
from app.claims.signals import token_f1
from app.core.logging import get_logger
from app.models.claim import Claim, ClaimRelation
from app.models.document import Document
from app.models.source import Source

log = get_logger(__name__)

NOTE_SOURCE_TYPES = ("note", "clip")

REASON_AGREES_WITH_OUTDATED = "agrees with an outdated claim"
REASON_CONTRADICTED = "contradicted by newer source"
REASON_SUPERSEDED = "superseded by newer source"


@dataclass
class StaleNote:
    claim_id: int
    statement: str
    document_id: uuid.UUID
    document_title: str
    reasons: list[str] = field(default_factory=list)
    evidence: list[Claim] = field(default_factory=list)
    asserted_at: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "reasons": list(self.reasons),
            "asserted_at": self.asserted_at.isoformat() if self.asserted_at else None,
            "evidence": [
                {
                    "claim_id": c.id,
                    "statement": c.statement,
                    "status": c.status,
                    "document_id": str(c.document_id),
                    "asserted_at": c.asserted_at.isoformat(),
                }
                for c in self.evidence
            ],
        }


def _note_claims(db: Session, user_id: uuid.UUID) -> list[tuple[Claim, str]]:
    """Active claims from note/clip sources, with their document titles."""
    rows = db.execute(
        select(Claim, Document.title)
        .join(Document, Document.id == Claim.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(
            Claim.user_id == user_id,
            Claim.status == "active",
            Source.source_type.in_(NOTE_SOURCE_TYPES),
        )
    ).all()
    return [(claim, title or "") for claim, title in rows]


def _note_document_ids(db: Session, user_id: uuid.UUID) -> set[uuid.UUID]:
    rows = (
        db.execute(
            select(Document.id)
            .join(Source, Source.id == Document.source_id)
            .where(Source.user_id == user_id, Source.source_type.in_(NOTE_SOURCE_TYPES))
        )
        .scalars()
        .all()
    )
    return set(rows)


def detect_stale_notes(
    db: Session,
    user_id: uuid.UUID,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[StaleNote]:
    """Notes whose content has been overtaken by newer sources."""
    limits = thresholds()
    note_ids = _note_document_ids(db, user_id)
    findings: dict[int, StaleNote] = {}

    def record(claim: Claim, title: str, reason: str, evidence: Claim) -> None:
        entry = findings.get(claim.id)
        if entry is None:
            entry = StaleNote(
                claim_id=claim.id,
                statement=claim.statement,
                document_id=claim.document_id,
                document_title=title,
                asserted_at=claim.asserted_at,
            )
            findings[claim.id] = entry
        if reason not in entry.reasons:
            entry.reasons.append(reason)
        if all(e.id != evidence.id for e in entry.evidence):
            entry.evidence.append(evidence)

    for note, title in _note_claims(db, user_id):
        # Rule 1: the note echoes a claim that has since been superseded or retracted.
        for neighbour, similarity in neighbours_of_claim(db, note, active_only=False):
            if neighbour.document_id in note_ids:
                continue
            if neighbour.status not in ("superseded", "retracted"):
                continue
            if similarity < limits["pair_sim"]:
                continue
            if token_f1(note.statement, neighbour.statement) < 0.5:
                continue
            record(note, title, REASON_AGREES_WITH_OUTDATED, neighbour)

    # Rule 2: a relation with a newer, non-note source says the note is out of date.
    relations = (
        db.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == user_id,
                ClaimRelation.relation.in_(("contradicts", "supersedes")),
            )
        )
        .scalars()
        .all()
    )

    for relation in relations:
        a = db.get(Claim, relation.a_claim_id)
        b = db.get(Claim, relation.b_claim_id)
        if a is None or b is None:
            continue
        for mine, theirs in ((a, b), (b, a)):
            if mine.document_id not in note_ids or mine.status != "active":
                continue
            if theirs.document_id in note_ids:
                continue
            if theirs.asserted_at <= mine.asserted_at:
                continue
            reason = REASON_CONTRADICTED if relation.relation == "contradicts" else REASON_SUPERSEDED
            record(mine, _title_of(db, mine), reason, theirs)

    results = sorted(findings.values(), key=lambda n: (n.asserted_at or datetime.min, n.claim_id))
    if since is not None:
        results = [n for n in results if n.asserted_at and n.asserted_at >= since]
    if offset:
        results = results[offset:]
    if limit is not None:
        results = results[:limit]
    return results


def _title_of(db: Session, claim: Claim) -> str:
    document = db.get(Document, claim.document_id)
    return (document.title if document else "") or ""


__all__ = [
    "NOTE_SOURCE_TYPES",
    "REASON_AGREES_WITH_OUTDATED",
    "REASON_CONTRADICTED",
    "REASON_SUPERSEDED",
    "StaleNote",
    "detect_stale_notes",
]
