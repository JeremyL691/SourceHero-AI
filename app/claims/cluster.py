"""Cluster assignment.

A claim joins the nearest existing cluster whose centroid it is similar enough to,
otherwise it starts one. Every claim therefore costs O(k) comparisons, independent of
how many claims the user has accumulated -- which is what keeps the ledger usable at
scale, and is asserted by a slow test in `tests/test_claims_scale.py`.

Thresholds differ per embedder because the spaces are not comparable: hashing vectors
put paraphrase pairs near 0.5, a real sentence-transformer near 0.9. The values are
tuned on the demo corpus.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.indexing.embeddings import get_embedder
from app.models.claim import Claim, ClaimCluster

log = get_logger(__name__)

NEIGHBOURS = 10

# Per-embedder defaults. env (CLUSTER_SIM etc.) wins when set.
THRESHOLDS: dict[str, dict[str, float]] = {
    # Measured on the demo corpus (86 claims; 574 sampled local pairs, 24 related):
    #   unrelated pairs  p50 0.025  p90 0.215  p95 0.309
    #   related pairs    p5  0.479  p25 0.499
    # The usable band starts around 0.43. 0.45 sits inside the gap; the 0.55 first
    # guess merged only 14 of 24 related pairs.
    "hashing": {"cluster_sim": 0.45, "pair_sim": 0.40, "dup_sim": 0.90, "carry_sim": 0.85, "topic_sim": 0.35},
    "fastembed": {
        "cluster_sim": 0.80,
        "pair_sim": 0.70,
        "dup_sim": 0.95,
        "carry_sim": 0.92,
        "topic_sim": 0.60,
    },
}


def thresholds(settings: Settings | None = None) -> dict[str, float]:
    """Resolve the similarity thresholds for the active embedder."""
    settings = settings or get_settings()
    base = THRESHOLDS.get("fastembed" if settings.embedding_provider == "fastembed" else "hashing")
    resolved = dict(base)
    for env_name, key in (
        ("cluster_sim", "cluster_sim"),
        ("pair_sim", "pair_sim"),
        ("dup_sim", "dup_sim"),
        ("carry_sim", "carry_sim"),
        ("topic_sim", "topic_sim"),
    ):
        override = getattr(settings, env_name, None)
        if override is not None:
            resolved[key] = float(override)
    return resolved


def nearest_clusters(db: Session, claim: Claim, *, k: int = NEIGHBOURS) -> list[tuple[ClaimCluster, float]]:
    """The `k` nearest clusters of this user, by centroid cosine similarity.

    Excludes claims from the same version: a document restating itself is a `duplicate`
    relation, not evidence that two separate sources agree.
    """
    if claim.embedding is None:
        return []
    vector = list(claim.embedding)
    stmt = (
        select(ClaimCluster, (1.0 - ClaimCluster.centroid.cosine_distance(vector)).label("sim"))
        .where(ClaimCluster.user_id == claim.user_id)
        .where(ClaimCluster.centroid.is_not(None))
        .order_by(ClaimCluster.centroid.cosine_distance(vector))
        .limit(k)
    )
    rows = db.execute(stmt).all()
    return [(cluster, float(sim)) for cluster, sim in rows]


def neighbours_of_claim(
    db: Session, claim: Claim, *, k: int = NEIGHBOURS, active_only: bool = True, same_user: bool = True
) -> list[tuple[Claim, float]]:
    """The `k` most similar claims, by embedding."""
    if claim.embedding is None:
        return []
    vector = list(claim.embedding)
    stmt = (
        select(Claim, (1.0 - Claim.embedding.cosine_distance(vector)).label("sim"))
        .where(Claim.id != claim.id)
        .where(Claim.embedding.is_not(None))
    )
    if same_user:
        stmt = stmt.where(Claim.user_id == claim.user_id)
    if active_only:
        stmt = stmt.where(Claim.status == "active")
    stmt = stmt.order_by(Claim.embedding.cosine_distance(vector)).limit(k)
    rows = db.execute(stmt).all()
    return [(row, float(sim)) for row, sim in rows]


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


def _recompute(cluster: ClaimCluster, db: Session) -> None:
    """Refresh size, document count and centroid from the cluster's members."""
    members = (
        db.execute(select(Claim).where(Claim.cluster_id == cluster.id, Claim.status == "active"))
        .scalars()
        .all()
    )
    cluster.size = len(members)
    cluster.doc_count = len({m.document_id for m in members})
    if members:
        stacked = np.array([list(m.embedding) for m in members if m.embedding is not None])
        if stacked.size:
            cluster.centroid = _unit(stacked.mean(axis=0)).tolist()


def assign_cluster(db: Session, claim: Claim) -> ClaimCluster:
    """Put `claim` in the nearest sufficiently-similar cluster, or create one."""
    limit = thresholds()["cluster_sim"]
    embedder = get_embedder()

    for cluster, similarity in nearest_clusters(db, claim):
        if similarity >= limit:
            old_cluster_id = claim.cluster_id
            claim.cluster_id = cluster.id
            db.flush()
            _recompute(cluster, db)
            if old_cluster_id is not None and old_cluster_id != cluster.id:
                old_cluster = db.get(ClaimCluster, old_cluster_id)
                if old_cluster is not None:
                    _recompute(old_cluster, db)
            db.flush()
            return cluster

    cluster = ClaimCluster(
        user_id=claim.user_id,
        label="",
        label_source="keywords",
        centroid=list(claim.embedding) if claim.embedding is not None else None,
        size=1,
        doc_count=1,
    )
    db.add(cluster)
    db.flush()
    claim.cluster_id = cluster.id
    if claim.embedding is None:
        claim.embedding = embedder.embed_query(claim.statement)
    db.flush()
    return cluster


def assign_clusters(db: Session, claims: list[Claim], *, sweeps: int = 2) -> None:
    """Assign a whole batch of claims, then re-sweep.

    Single-pass assignment is order-dependent: a claim that arrives before its
    future cluster-mate creates its own cluster and is never reconsidered, so two
    sides of a disagreement end up in different clusters -- and then no cluster can
    ever hold both camps. Re-sweeping lets late arrivals join clusters that only
    existed after the first pass.
    """
    pending = [c for c in claims if c.id is not None]
    for _ in range(max(1, sweeps)):
        before = [(c.id, c.cluster_id) for c in pending]
        for claim in pending:
            assign_cluster(db, claim)
        after = [(c.id, c.cluster_id) for c in pending]
        if after == before:
            break


def merge_clusters(db: Session, keep: ClaimCluster, drop: ClaimCluster) -> ClaimCluster:
    """Fold `drop` into `keep`, then delete it.

    Triggered when relation judging connects claims from two clusters with high
    confidence -- the two clusters were about the same thing after all.
    """
    members = db.execute(select(Claim).where(Claim.cluster_id == drop.id)).scalars().all()
    for member in members:
        member.cluster_id = keep.id
    db.flush()
    _recompute(keep, db)
    db.flush()
    db.delete(drop)
    db.flush()
    return keep


def recount_conflicts(db: Session, cluster_id: int) -> int:
    """Unresolved contradictions inside a cluster, used for `conflict_count`."""
    from sqlalchemy import func

    cluster = db.get(ClaimCluster, cluster_id)
    if cluster is None:
        return 0
    members = (
        db.execute(select(Claim.id).where(Claim.cluster_id == cluster_id, Claim.status == "active"))
        .scalars()
        .all()
    )
    if not members:
        cluster.conflict_count = 0
        return 0
    from app.models.claim import ClaimRelation

    count = db.execute(
        select(func.count())
        .select_from(ClaimRelation)
        .where(
            ClaimRelation.user_id == cluster.user_id,
            ClaimRelation.relation == "contradicts",
            ClaimRelation.resolved_at.is_(None),
            # `user_label != 'rejected'` is NULL -- not true -- for every unlabelled
            # row, so it would exclude exactly the relations we want to count.
            or_(ClaimRelation.user_label.is_(None), ClaimRelation.user_label != "rejected"),
            (ClaimRelation.a_claim_id.in_(members)) | (ClaimRelation.b_claim_id.in_(members)),
        )
    ).scalar_one()
    cluster.conflict_count = int(count)
    db.flush()
    return cluster.conflict_count


def cluster_label(db: Session, cluster: ClaimCluster) -> str:
    """Top three content tokens in the cluster, by frequency across all clusters.

    Relative rather than absolute frequency so a word shared by every cluster does
    not become every cluster's name.
    """
    members = (
        db.execute(select(Claim.statement).where(Claim.cluster_id == cluster.id, Claim.status == "active"))
        .scalars()
        .all()
    )
    if not members:
        return ""

    from app.claims.signals import content_tokens

    own: dict[str, int] = {}
    for statement in members:
        for token in set(content_tokens(statement)):
            own[token] = own.get(token, 0) + 1

    all_clusters = (
        db.execute(select(Claim.statement).where(Claim.user_id == cluster.user_id, Claim.status == "active"))
        .scalars()
        .all()
    )
    total: dict[str, int] = {}
    for statement in all_clusters:
        for token in set(content_tokens(statement)):
            total[token] = total.get(token, 0) + 1

    scored = sorted(own, key=lambda t: own[t] / (1 + total.get(t, 1)), reverse=True)
    return " ".join(scored[:3])


__all__ = [
    "NEIGHBOURS",
    "THRESHOLDS",
    "assign_cluster",
    "cluster_label",
    "merge_clusters",
    "nearest_clusters",
    "neighbours_of_claim",
    "recount_conflicts",
    "thresholds",
]
