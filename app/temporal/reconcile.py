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
    numbers_differ,
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


def _match_claims_between_versions(
    previous_claims: list[Claim],
    current_claims: list[Claim],
    carry_sim: float,
    pair_sim: float,
    is_suspect: bool,
) -> list[tuple[Claim, Claim | None, str]]:
    """Global 1-to-1 matching between consecutive version claims.

    Returns list of (previous_claim, successor_or_none, outcome) where
    outcome is one of 'carried', 'superseded', 'retracted', or 'suspect_retained'.
    """
    matched_previous: set[int] = set()
    matched_current: set[int] = set()
    matches: list[tuple[Claim, Claim | None, str]] = []

    # 1. Exact identical text matches first (carried)
    for prev in previous_claims:
        for curr in current_claims:
            if curr.id not in matched_current and identical_normalized(prev.statement, curr.statement):
                matched_previous.add(prev.id)
                matched_current.add(curr.id)
                matches.append((prev, curr, "carried"))
                break

    # 2. Score all remaining candidate pairs and sort descending
    unmatched_prev = [p for p in previous_claims if p.id not in matched_previous]
    unmatched_curr = [c for c in current_claims if c.id not in matched_current]

    candidate_pairs = []
    for prev in unmatched_prev:
        for curr in unmatched_curr:
            score = cosine(list(prev.embedding or []), list(curr.embedding or []))
            candidate_pairs.append((score, prev, curr))

    candidate_pairs.sort(key=lambda x: x[0], reverse=True)

    for score, prev, curr in candidate_pairs:
        if prev.id in matched_previous or curr.id in matched_current:
            continue

        num_diff = numbers_differ(prev.statement, curr.statement)
        conflict = (
            numbers_conflict(prev.statement, curr.statement) is True
            or num_diff
            or date_conflict(prev.statement, curr.statement) is True
        )

        outcome = None
        if conflict and score >= pair_sim:
            outcome = "superseded"
        elif score >= carry_sim and not conflict and not num_diff:
            outcome = "carried"
        elif score >= max(0.70, pair_sim) and token_f1(prev.statement, curr.statement) >= 0.3:
            outcome = "superseded"

        if outcome is not None:
            matched_previous.add(prev.id)
            matched_current.add(curr.id)
            matches.append((prev, curr, outcome))

    # 3. Remaining unmatched previous claims
    for prev in previous_claims:
        if prev.id not in matched_previous:
            if is_suspect:
                matches.append((prev, None, "suspect_retained"))
            else:
                matches.append((prev, None, "retracted"))

    return matches


def _best_match(
    previous: Claim, candidates: list[Claim], carry_sim: float, pair_sim: float
) -> tuple[Claim | None, str]:
    """Single-claim helper delegating to _match_claims_between_versions."""
    matches = _match_claims_between_versions([previous], candidates, carry_sim, pair_sim, is_suspect=False)
    if matches:
        return matches[0][1], matches[0][2]
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

    own_claim_ids = {c.id for v in versions for c in _claims_of(db, v.id)}
    externally_superseded: set[int] = set()
    for claim in db.execute(
        select(Claim).where(Claim.document_id == document_id, Claim.superseded_by.is_not(None))
    ).scalars():
        if claim.superseded_by not in own_claim_ids:
            externally_superseded.add(claim.id)

    # 1. Reset this document's own bookkeeping for claims that are not externally superseded.
    own = db.execute(select(Claim).where(Claim.document_id == document_id)).scalars().all()
    for claim in own:
        if claim.id not in externally_superseded:
            claim.status = "active"
            claim.valid_to = None
            claim.superseded_by = None

    # Load existing reconcile-origin events to preserve event IDs and observed_at.
    existing_events = (
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
    # Map key -> ClaimEvent for events created by reconcile
    reconcile_events: dict[tuple, ClaimEvent] = {}
    for event in existing_events:
        is_reconcile = (event.meta and event.meta.get("origin") == RECONCILE_ORIGIN) or (
            event.event == "conflict_resolved" and event.meta and "cause" in event.meta
        )
        if is_reconcile:
            if event.event == "superseded":
                key = ("superseded", event.claim_id, event.related_claim_id, event.version_id)
            elif event.event == "retracted":
                key = ("retracted", event.claim_id, event.version_id)
            elif event.event == "conflict_resolved":
                key = ("conflict_resolved", frozenset({event.claim_id, event.related_claim_id} - {None}))
            else:
                continue
            reconcile_events[key] = event

    # 2. Walk consecutive version pairs.
    for previous_version, current_version in zip(versions, versions[1:], strict=False):
        previous_claims = [
            c for c in _claims_of(db, previous_version.id) if c.id not in externally_superseded
        ]
        current_claims = _claims_of(db, current_version.id)

        # Detect suspect versions
        prev_len = len(previous_version.clean_text or "")
        curr_len = len(current_version.clean_text or "")
        is_suspect = (prev_len > 0 and curr_len < 0.3 * prev_len) or (
            len(previous_claims) >= 5 and len(current_claims) == 0
        )
        if is_suspect:
            diff_stats = dict(current_version.diff_stats or {})
            diff_stats["suspect"] = True
            current_version.diff_stats = diff_stats
            log.warning(
                "reconcile_suspect_version document_id=%s version_id=%s prev_len=%d curr_len=%d",
                document_id,
                current_version.id,
                prev_len,
                curr_len,
            )

        matches = _match_claims_between_versions(
            previous_claims,
            current_claims,
            limits["carry_sim"],
            limits["pair_sim"],
            is_suspect,
        )

        for previous, successor, outcome in matches:
            if outcome == "suspect_retained":
                # Kept as active, no retracted event
                continue
            elif outcome == "retracted":
                previous.status = "retracted"
                previous.valid_to = current_version.effective_at
                key = ("retracted", previous.id, current_version.id)
                existing = reconcile_events.pop(key, None)
                if existing is not None:
                    existing.at = current_version.effective_at
                else:
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
            elif outcome == "carried":
                previous.status = "carried"
                if successor is not None:
                    previous.superseded_by = successor.id
                stats.carried += 1
            elif outcome == "superseded":
                previous.status = "superseded"
                if successor is not None:
                    previous.superseded_by = successor.id
                previous.valid_to = current_version.effective_at
                key = ("superseded", previous.id, successor.id if successor else None, current_version.id)
                existing = reconcile_events.pop(key, None)
                if existing is not None:
                    existing.at = current_version.effective_at
                else:
                    db.add(
                        ClaimEvent(
                            user_id=previous.user_id,
                            claim_id=previous.id,
                            event="superseded",
                            at=current_version.effective_at,
                            observed_at=now,
                            related_claim_id=successor.id if successor else None,
                            version_id=current_version.id,
                            meta={"origin": RECONCILE_ORIGIN},
                        )
                    )
                stats.superseded += 1

    db.flush()

    # 3. Re-evaluate every contradiction that touches this document.
    stats.conflicts_resolved, stats.conflicts_opened = _reconcile_conflicts(
        db, document, versions, now, reconcile_events
    )
    db.flush()

    # Any remaining event in reconcile_events was not matched in this pass: delete it.
    for stale_event in reconcile_events.values():
        db.delete(stale_event)
    db.flush()

    from app.claims.cluster import recount_conflicts

    for version in versions:
        for claim in _claims_of(db, version.id):
            if claim.cluster_id is not None:
                recount_conflicts(db, claim.cluster_id)
    db.flush()
    return stats


def _reconcile_conflicts(
    db: Session,
    document: Document,
    versions: list[DocumentVersion],
    now,
    reconcile_events: dict[tuple, ClaimEvent],
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
    opened = 0
    for relation in relations:
        if relation.user_label == "rejected":
            continue

        a = db.get(Claim, relation.a_claim_id)
        b = db.get(Claim, relation.b_claim_id)
        if a is None or b is None:
            continue
        # A contradiction only stands while both sides are live statements.
        still_open = a.status in ("active", "carried") and b.status in ("active", "carried")
        if still_open:
            if relation.resolved_at is not None:
                relation.resolved_at = None
                opened += 1
        else:
            # Resolving claim world time
            if a.status not in ("active", "carried"):
                inactive_claim, other_claim = a, b
            else:
                inactive_claim, other_claim = b, a

            world_time = None
            if inactive_claim.superseded_by:
                successor = db.get(Claim, inactive_claim.superseded_by)
                if successor and successor.asserted_at:
                    world_time = successor.asserted_at
            if world_time is None:
                world_time = inactive_claim.valid_to or inactive_claim.asserted_at or now

            if relation.resolved_at is None:
                relation.resolved_at = now
                resolved += 1

            key = ("conflict_resolved", frozenset({inactive_claim.id, other_claim.id} - {None}))
            existing = reconcile_events.pop(key, None)
            if existing is not None:
                existing.at = world_time
                existing.claim_id = inactive_claim.id
                existing.related_claim_id = other_claim.id
                existing.meta = {"origin": RECONCILE_ORIGIN, "cause": f"claim {a.status}/{b.status}"}
            else:
                db.add(
                    ClaimEvent(
                        user_id=relation.user_id,
                        claim_id=inactive_claim.id,
                        event="conflict_resolved",
                        at=world_time,
                        observed_at=now,
                        related_claim_id=other_claim.id,
                        meta={"origin": RECONCILE_ORIGIN, "cause": f"claim {a.status}/{b.status}"},
                    )
                )
    return resolved, opened


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
