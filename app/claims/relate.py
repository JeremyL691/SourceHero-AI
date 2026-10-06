"""Relation judging.

Two stages, and the order is the point. The heuristic runs first and decides the
clear cases -- a same-unit numeric conflict, an asymmetric negation, a rescheduled
date. Only pairs the heuristic could not settle are sent to the model, in batches. A
live LLM therefore costs a fraction of a call per pair, and the offline path is
exactly as good as it was.

The bound that keeps this usable is `MAX_JUDGE_PAIRS_PER_CLAIM`: each new claim
compares against at most that many candidates, found by embedding search, so judging
cost is O(1) in the size of the ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.cluster import thresholds
from app.claims.signals import (
    date_conflict,
    negation_asymmetry,
    numbers_conflict,
    numbers_differ,
    temporal_update_cue,
    token_f1,
)
from app.core import time as clock
from app.core.logging import get_logger
from app.models.claim import Claim, ClaimEvent, ClaimRelation

log = get_logger(__name__)

MAX_JUDGE_PAIRS_PER_CLAIM = 5
MIN_F1_FOR_RELATION = 0.30

RELATIONS = ("supports", "contradicts", "refines", "supersedes", "duplicate")

# Sources whose claims stay `active` even when contradicted or superseded: the user's
# own notes. Changing a note's status would edit the user's writing; M9 flags them as
# potentially outdated instead.
NOTE_SOURCE_TYPES = ("note", "clip")


@dataclass
class RelationDraft:
    relation: str
    direction: str | None
    confidence: float
    signals: dict = field(default_factory=dict)
    rationale: str = ""


def candidate_pairs(
    db: Session, claim: Claim, *, limit: int = MAX_JUDGE_PAIRS_PER_CLAIM
) -> list[tuple[Claim, float]]:
    """The claims most worth comparing `claim` against.

    Cross-document only: two versions of the same page are the same lineage, and that
    is the temporal reconcile's job (M9), not cross-source relation judging.
    Uses pgvector kNN in SQL to ensure cross-document candidates are retrieved even
    when the source document has many claims.
    """
    if claim.embedding is None:
        return []

    limit_sim = thresholds()["pair_sim"]
    vector = list(claim.embedding)

    stmt = (
        select(Claim, (1.0 - Claim.embedding.cosine_distance(vector)).label("sim"))
        .where(Claim.user_id == claim.user_id)
        .where(Claim.status.in_(("active", "carried")))
        .where(Claim.document_id != claim.document_id)
        .where(Claim.embedding.is_not(None))
        .order_by(Claim.embedding.cosine_distance(vector))
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    out: list[tuple[Claim, float]] = []
    for c, sim in rows:
        sim_val = float(sim)
        if sim_val >= limit_sim:
            out.append((c, sim_val))
    return out


def heuristic_relation(a: Claim, b: Claim, similarity: float) -> RelationDraft | None:
    """Decide a pair from surface signals alone, or return None to defer to the LLM."""
    limits = thresholds()
    numbers = numbers_conflict(a.statement, b.statement)
    dates = date_conflict(a.statement, b.statement)
    negation = negation_asymmetry(a.statement, b.statement)
    overlap = token_f1(a.statement, b.statement)

    signals = {
        "nc": numbers,
        "dc": dates,
        "neg": negation,
        "f1": round(overlap, 4),
        "sim": round(similarity, 4),
    }

    num_diff = numbers_differ(a.statement, b.statement)
    conflicting = numbers is True or num_diff or dates is True or negation
    if conflicting and overlap >= MIN_F1_FOR_RELATION:
        if a.asserted_at is not None and b.asserted_at is not None:
            newer, older = order_by_asserted_at(a, b)
            rescheduled = (dates is True or numbers is True or num_diff) and temporal_update_cue(
                newer.statement
            )
            if rescheduled and newer.asserted_at > older.asserted_at:
                return RelationDraft(
                    relation="supersedes",
                    direction="a_to_b" if newer.id == a.id else "b_to_a",
                    confidence=0.65,
                    signals=signals,
                    rationale="the newer claim signals an update to the older one",
                )
        fired = sum(1 for flag in (numbers or num_diff, dates, negation) if flag is True)
        return RelationDraft(
            relation="contradicts",
            direction=None,
            confidence=0.6 + 0.1 * (fired - 1),
            signals=signals,
            rationale="the two statements cannot both be true",
        )

    if similarity >= limits["dup_sim"] and numbers is not True and not num_diff:
        return RelationDraft("duplicate", None, 0.7, signals, "near-identical statements")

    if overlap >= 0.5 and numbers is not True and not num_diff and dates is not True and not negation:
        return RelationDraft(
            relation="supports",
            direction=None,
            confidence=0.55 + 0.2 * (overlap - 0.5),
            signals=signals,
            rationale="the statements agree",
        )

    return None


def order_by_asserted_at(a: Claim, b: Claim) -> tuple[Claim, Claim]:
    """(newer, older). Ties break on id so the order is stable."""
    if a.asserted_at is None and b.asserted_at is None:
        return (a, b) if (a.id or 0) >= (b.id or 0) else (b, a)
    if a.asserted_at is None:
        return b, a
    if b.asserted_at is None:
        return a, b
    if (a.asserted_at, a.id or 0) >= (b.asserted_at, b.id or 0):
        return a, b
    return b, a


def store_relation(
    db: Session, user_id: uuid.UUID, a: Claim, b: Claim, draft: RelationDraft, *, method: str
) -> ClaimRelation | None:
    """Upsert a relation, ordered by id so the pair has one canonical orientation.

    Returns None when the relation already exists and a human has labelled it: a user
    decision outranks anything the pipeline re-derives.
    """
    if a.id == b.id:
        return None
    first, second = (a, b) if a.id < b.id else (b, a)

    existing = db.execute(
        select(ClaimRelation).where(
            ClaimRelation.user_id == user_id,
            ClaimRelation.a_claim_id == first.id,
            ClaimRelation.b_claim_id == second.id,
        )
    ).scalar_one_or_none()

    if existing is not None and existing.user_label is not None:
        return existing

    if existing is None:
        existing = ClaimRelation(user_id=user_id, a_claim_id=first.id, b_claim_id=second.id)
        db.add(existing)
    existing.relation = draft.relation
    # Direction is stored against the canonical (a, b) ordering.
    if draft.direction is None:
        existing.direction = None
    elif first is a:
        existing.direction = draft.direction
    else:
        existing.direction = "b_to_a" if draft.direction == "a_to_b" else "a_to_b"
    existing.confidence = draft.confidence
    existing.method = method
    existing.signals = draft.signals
    existing.rationale = draft.rationale
    db.flush()
    return existing


def judge_relations(db: Session, claim_ids: list[int]) -> dict:
    """Judge every candidate pair for the given claims."""
    stats = {"pairs": 0, "contradicts": 0, "supersedes": 0, "supports": 0, "duplicate": 0, "deferred": 0}
    if not claim_ids:
        return stats

    claims = list(db.execute(select(Claim).where(Claim.id.in_(claim_ids))).scalars())
    if not claims:
        return stats
    user_id = claims[0].user_id
    seen: set[tuple[int, int]] = set()
    new_relations: list[ClaimRelation] = []

    for claim in claims:
        for other, similarity in candidate_pairs(db, claim):
            key = (min(claim.id, other.id), max(claim.id, other.id))
            if key in seen:
                continue
            seen.add(key)

            draft = heuristic_relation(claim, other, similarity)
            if draft is None:
                stats["deferred"] += 1
                continue

            stored = store_relation(db, claim.user_id, claim, other, draft, method="heuristic")
            if stored is None:
                continue
            new_relations.append(stored)
            stats["pairs"] += 1
            stats[draft.relation] = stats.get(draft.relation, 0) + 1
            _apply_side_effects(db, claim, other, stored)

    _merge_across_clusters(db, user_id, new_relations, stats)
    db.flush()
    return stats


CLUSTER_MERGE_MIN_CONF = 0.75


def merge_confidence() -> float:
    """How confident a relation must be before it is allowed to merge two clusters.

    A weak relation must not be able to fuse two genuinely different topics: the
    cost of a wrong merge is that every future claim about either one joins the same
    camp. Default is 0.75 (higher than heuristic contradiction's 0.6).
    """
    from app.core.config import get_settings

    return float(
        getattr(get_settings(), "merge_confidence", CLUSTER_MERGE_MIN_CONF) or CLUSTER_MERGE_MIN_CONF
    )


def _merge_across_clusters(
    db: Session, user_id: uuid.UUID, relations: list[ClaimRelation], stats: dict
) -> None:
    """Fuse clusters whose claims are tied by a strong relation.

    Relation judging finds contradictions by claim-to-claim similarity; the two claims
    can land in different clusters, and then the cluster view cannot show the
    disagreement at all. A relation confident enough to assert "these are about the
    same thing and disagree" is also evidence the clusters belong together.
    """
    from app.claims.cluster import merge_clusters
    from app.models.claim import Claim, ClaimCluster

    limit = merge_confidence()
    merged = 0
    for relation in relations:
        if relation.user_id != user_id:
            continue
        if relation.user_label == "rejected":
            continue
        is_confident = (relation.confidence >= limit) or (relation.method in ("llm", "user"))
        if not is_confident:
            continue
        a = db.get(Claim, relation.a_claim_id)
        b = db.get(Claim, relation.b_claim_id)
        if a is None or b is None:
            continue
        if a.user_id != user_id or b.user_id != user_id:
            continue
        if a.cluster_id is None or b.cluster_id is None or a.cluster_id == b.cluster_id:
            continue
        keep_row = db.get(ClaimCluster, a.cluster_id)
        drop_row = db.get(ClaimCluster, b.cluster_id)
        if keep_row is None or drop_row is None:
            continue
        # Merge into the older cluster so ids stay stable across runs.
        keep, drop = (keep_row, drop_row) if keep_row.id <= drop_row.id else (drop_row, keep_row)
        merge_clusters(db, keep, drop)
        merged += 1

    if merged:
        stats["clusters_merged"] = merged


def _source_type_of(db: Session, claim: Claim) -> str:
    from app.models.document import Document
    from app.models.source import Source

    document = db.get(Document, claim.document_id)
    if document is None:
        return ""
    source = db.get(Source, document.source_id)
    return source.source_type if source else ""


def _apply_side_effects(db: Session, a: Claim, b: Claim, relation: ClaimRelation) -> None:
    """Write the events and status changes a newly found relation implies."""
    if relation.relation == "contradicts" and relation.resolved_at is None:
        _open_conflict(db, a, b)
    elif relation.relation == "supersedes":
        claim_a = a if a.id == relation.a_claim_id else b
        claim_b = b if b.id == relation.b_claim_id else a
        newer = claim_a if relation.direction == "a_to_b" else claim_b
        older = claim_b if newer is claim_a else claim_a
        if _source_type_of(db, older) not in NOTE_SOURCE_TYPES:
            apply_supersede(db, older, newer)


def _open_conflict(db: Session, a: Claim, b: Claim) -> None:
    now = clock.utcnow()
    when = max(a.asserted_at, b.asserted_at)
    for claim, other in ((a, b), (b, a)):
        exists = db.execute(
            select(ClaimEvent).where(
                ClaimEvent.claim_id == claim.id,
                ClaimEvent.event == "conflict_opened",
                ClaimEvent.related_claim_id == other.id,
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(
                ClaimEvent(
                    user_id=claim.user_id,
                    claim_id=claim.id,
                    event="conflict_opened",
                    at=when,
                    observed_at=now,
                    related_claim_id=other.id,
                )
            )
    from app.claims.cluster import recount_conflicts

    for claim in (a, b):
        if claim.cluster_id is not None:
            recount_conflicts(db, claim.cluster_id)
    db.flush()


def apply_supersede(db: Session, older: Claim, newer: Claim) -> None:
    """Mark `older` superseded and close the conflicts it was party to."""
    now = clock.utcnow()
    if older.status == "superseded":
        return
    older.status = "superseded"
    older.superseded_by = newer.id
    older.valid_to = newer.asserted_at
    db.flush()

    db.add(
        ClaimEvent(
            user_id=older.user_id,
            claim_id=older.id,
            event="superseded",
            at=newer.asserted_at,
            observed_at=now,
            related_claim_id=newer.id,
        )
    )
    resolve_conflicts_for(db, older)


def resolve_conflicts_for(db: Session, claim: Claim) -> int:
    """Close every unresolved contradiction involving `claim`, and say so."""
    now = clock.utcnow()
    relations = (
        db.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == claim.user_id,
                ClaimRelation.relation == "contradicts",
                ClaimRelation.resolved_at.is_(None),
                (ClaimRelation.a_claim_id == claim.id) | (ClaimRelation.b_claim_id == claim.id),
            )
        )
        .scalars()
        .all()
    )

    for relation in relations:
        relation.resolved_at = now
        db.add(
            ClaimEvent(
                user_id=claim.user_id,
                claim_id=claim.id,
                event="conflict_resolved",
                at=now,
                observed_at=now,
                related_claim_id=(
                    relation.b_claim_id if relation.a_claim_id == claim.id else relation.a_claim_id
                ),
                meta={"cause": "claim superseded"},
            )
        )

    from app.claims.cluster import recount_conflicts

    if claim.cluster_id is not None:
        recount_conflicts(db, claim.cluster_id)
    db.flush()
    return len(relations)


def record_assertion(db: Session, claim: Claim, *, at: datetime) -> None:
    """The `asserted` event every new claim emits."""
    db.add(
        ClaimEvent(
            user_id=claim.user_id,
            claim_id=claim.id,
            event="asserted",
            at=at,
            observed_at=clock.utcnow(),
            version_id=claim.version_id,
        )
    )


__all__ = [
    "MAX_JUDGE_PAIRS_PER_CLAIM",
    "merge_confidence",
    "NOTE_SOURCE_TYPES",
    "RELATIONS",
    "RelationDraft",
    "apply_supersede",
    "candidate_pairs",
    "heuristic_relation",
    "judge_relations",
    "order_by_asserted_at",
    "record_assertion",
    "resolve_conflicts_for",
    "store_relation",
]
