"""Whole-document reconciliation: what happened to each claim as the document evolved.

Recomputed from scratch every time, rather than appended incrementally, and that is the
central design decision. An incremental pass cannot answer "what if this snapshot had
arrived first?" -- and a Wayback backfill routinely inserts a version that is
chronologically *earlier* than what we already have, which would leave the ledger
permanently wrong. Recomputing makes execution order irrelevant: the same set of
versions always produces the same lifecycle.

Three outcomes per claim, decided by comparing each version's claims with the next
version's:

* `carried`   -- said again, unchanged. The old row is marked `carried` and stops
                 participating in conflicts and camps.
* `superseded`-- restated with something different (a new value, a new date). The old
                 row is marked `superseded` with `valid_to` and `superseded_by` set.
* `retracted` -- simply gone in the next version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.claims.cluster import thresholds
from app.claims.signals import (
    date_conflict,
    identical_normalized,
    numbers_conflict,
    token_f1,
)
from app.core import time as clock
from app.core.logging import get_logger
from app.indexing.embeddings import cosine
from app.models.claim import Claim, ClaimEvent, ClaimRelation
from app.models.document import Document, DocumentVersion

log = get_logger(__name__)

RECONCILE_ORIGIN = "reconcile"


@dataclass
class ReconcileStats:
    carried: int = 0
    superseded: int = 0
    retracted: int = 0
    conflicts_resolved: int = 0
    conflicts_opened: int = 0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "carried": self.carried,
            "superseded": self.superseded,
            "retracted": self.retracted,
            "conflicts_resolved": self.conflicts_resolved,
            "conflicts_opened": self.conflicts_opened,
        }


def _versions_of(db: Session, document_id: uuid.UUID) -> list[DocumentVersion]:
    return list(
        db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.effective_at, DocumentVersion.id)
        ).scalars()
    )


def _claims_of(db: Session, version_id: int) -> list[Claim]:
    return list(
        db.execute(select(Claim).where(Claim.version_id == version_id).order_by(Claim.char_start)).scalars()
    )


def _best_match(
    previous: Claim, candidates: list[Claim], carry_sim: float, pair_sim: float
) -> tuple[Claim | None, str]:
    """The best successor for `previous`, and how we concluded that.

    Exact text first: a sentence that is byte-identical across versions is certainly
    carried, and no fuzzy score should be able to talk us out of it.
    """
    identical = next((c for c in candidates if identical_normalized(previous.statement, c.statement)), None)
    if identical is not None:
        return identical, "carried"

    best: Claim | None = None
    best_score = -1.0
    for candidate in candidates:
        score = cosine(list(previous.embedding or []), list(candidate.embedding or []))
        if score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None, "retracted"

    conflict = (
        numbers_conflict(previous.statement, best.statement) is True
        or date_conflict(previous.statement, best.statement) is True
    )
    if conflict and best_score >= pair_sim:
        return best, "superseded"
    if best_score >= carry_sim and not conflict:
        return best, "carried"
    if best_score >= pair_sim and token_f1(previous.statement, best.statement) >= 0.3:
        return best, "superseded"
    return None, "retracted"


def reconcile_document(db: Session, document_id: uuid.UUID) -> ReconcileStats:
    """Recompute the claim lifecycle for one document. Idempotent."""
    stats = ReconcileStats()
    document = db.get(Document, document_id)
    if document is None:
        return stats

    versions = _versions_of(db, document_id)
    if not versions:
        return stats
    now = clock.utcnow()
    limits = thresholds()

    # Only a *cross-document* supersession is exempt: another source already told us
    # this was outdated, and reconciling this document must not un-supersede it. A claim that this document's
    # own previous reconcile run superseded is ours to recompute -- treating it as
    # external would reset its status to active and then skip it, so a second run
    # would leave every superseded claim silently marked active.
    # `superseded_by` holds a *claim* id, so the comparison has to be against this
    # document's claims -- comparing it against version ids made every claim look
    # externally superseded, and the loop then skipped all of them.
    own_claim_ids = {c.id for v in versions for c in _claims_of(db, v.id)}
    externally_superseded: set[int] = set()
    for claim in db.execute(
        select(Claim).where(Claim.document_id == document_id, Claim.superseded_by.is_not(None))
    ).scalars():
        if claim.superseded_by not in own_claim_ids:
            externally_superseded.add(claim.id)

    # 1. Reset this document's own bookkeeping. Events this pass owns are removed so a
    #    re-run cannot double-count; `asserted` events belong to extraction and stay.
    own = db.execute(select(Claim).where(Claim.document_id == document_id)).scalars().all()
    for claim in own:
        claim.status = "active"
        claim.valid_to = None
        if claim.id not in externally_superseded:
            claim.superseded_by = None

    stale_events = (
        db.execute(
            select(ClaimEvent)
            .join(Claim, Claim.id == ClaimEvent.claim_id)
            .where(
                Claim.document_id == document_id,
                ClaimEvent.event.in_(("superseded", "retracted", "conflict_resolved")),
            )
        )
        .scalars()
        .all()
    )
    for event in stale_events:
        db.delete(event)
    db.flush()

    # 2. Walk consecutive version pairs.
    for previous_version, current_version in zip(versions, versions[1:], strict=False):
        previous_claims = _claims_of(db, previous_version.id)
        current_claims = _claims_of(db, current_version.id)
        claimed: set[int] = set()

        for previous in previous_claims:
            if previous.id in externally_superseded:
                continue
            candidates = [c for c in current_claims if c.id not in claimed]
            successor, outcome = _best_match(previous, candidates, limits["carry_sim"], limits["pair_sim"])
            if successor is None:
                previous.status = "retracted"
                previous.valid_to = current_version.effective_at
                db.add(
                    ClaimEvent(
                        user_id=previous.user_id,
                        claim_id=previous.id,
                        event="retracted",
                        at=current_version.effective_at,
                        observed_at=now,
                        version_id=current_version.id,
                        meta={"origin": RECONCILE_ORIGIN},
                    )
                )
                stats.retracted += 1
                continue

            claimed.add(successor.id)
            if outcome == "carried":
                # `carried` is bookkeeping only: no user-facing event, because
                # "the same sentence is still there" is not a change worth showing.
                previous.status = "carried"
                previous.superseded_by = successor.id
                stats.carried += 1
            else:
                previous.status = "superseded"
                previous.superseded_by = successor.id
                previous.valid_to = current_version.effective_at
                db.add(
                    ClaimEvent(
                        user_id=previous.user_id,
                        claim_id=previous.id,
                        event="superseded",
                        at=current_version.effective_at,
                        observed_at=now,
                        related_claim_id=successor.id,
                        version_id=current_version.id,
                        meta={"origin": RECONCILE_ORIGIN},
                    )
                )
                stats.superseded += 1

    db.flush()

    # 3. Re-evaluate every contradiction that touches this document.
    stats.conflicts_resolved, stats.conflicts_opened = _reconcile_conflicts(db, document, versions, now)
    db.flush()

    from app.claims.cluster import recount_conflicts

    for version in versions:
        for claim in _claims_of(db, version.id):
            if claim.cluster_id is not None:
                recount_conflicts(db, claim.cluster_id)
    db.flush()
    return stats


def _reconcile_conflicts(
    db: Session, document: Document, versions: list[DocumentVersion], now
) -> tuple[int, int]:
    """Open or close contradictions depending on whether a side is still current."""
    claim_ids = [c.id for version in versions for c in _claims_of(db, version.id)]
    if not claim_ids:
        return 0, 0

    relations = (
        db.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == document.user_id,
                ClaimRelation.relation == "contradicts",
                (ClaimRelation.a_claim_id.in_(claim_ids)) | (ClaimRelation.b_claim_id.in_(claim_ids)),
            )
        )
        .scalars()
        .all()
    )

    resolved = 0
    for relation in relations:
        a = db.get(Claim, relation.a_claim_id)
        b = db.get(Claim, relation.b_claim_id)
        if a is None or b is None:
            continue
        # A contradiction only stands while both sides are live statements.
        still_open = a.status in ("active", "carried") and b.status in ("active", "carried")
        if still_open and relation.resolved_at is not None:
            relation.resolved_at = None
        elif not still_open and relation.resolved_at is None:
            relation.resolved_at = now
            resolved += 1
            db.add(
                ClaimEvent(
                    user_id=relation.user_id,
                    claim_id=a.id if a.status not in ("active", "carried") else b.id,
                    event="conflict_resolved",
                    at=now,
                    observed_at=now,
                    related_claim_id=b.id if a.status not in ("active", "carried") else a.id,
                    meta={"cause": f"claim {a.status}/{b.status}"},
                )
            )
    return resolved, 0


def schedule_reconcile(db: Session, document_id: uuid.UUID, *, enqueue) -> None:
    """Queue a debounced reconcile for a document."""
    enqueue(
        "reconcile_document",
        {"document_id": str(document_id)},
        dedupe_key=f"reconcile:{document_id}",
    )


def documents_needing_reconcile(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Documents with more than one version -- the only ones a reconcile changes."""
    rows = (
        db.execute(
            select(Document.id)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .where(Document.user_id == user_id)
            .group_by(Document.id)
            .having(func.count(DocumentVersion.id) > 1)
        )
        .scalars()
        .all()
    )
    return list(rows)


__all__ = [
    "RECONCILE_ORIGIN",
    "ReconcileStats",
    "documents_needing_reconcile",
    "reconcile_document",
    "schedule_reconcile",
]
