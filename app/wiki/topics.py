"""Topic planning: clusters in, wiki pages out.

The wiki is a build. Planning is its dependency graph step: it groups clusters into
topics, ensures one page per topic, and marks pages dirty when their input hash moves.

Stability is the property that matters. Re-planning must not rename or renumber
pages -- a slug is a permanent external identifier, and a page whose topic shifted
slightly should be recompiled, not recreated. A topic is therefore reused whenever its
membership overlaps the existing one by at least half.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claims.cluster import thresholds
from app.core.logging import get_logger
from app.models.claim import Claim, ClaimCluster
from app.models.wiki import Topic, WikiPage

log = get_logger(__name__)

MIN_CLAIMS_PER_TOPIC = 3
MIN_DOCUMENTS_PER_TOPIC = 2
# A topic is reused when at least this fraction of its clusters overlap an existing one.
REUSE_JACCARD = 0.5
# Above this many clusters, pairwise comparison is replaced by centroid kNN.
PAIRWISE_LIMIT = 2000

# Entity-shaped tokens: "HX-2", "Orchid-7". These are the most reliable glue between
# claims that share almost no words.
#
# The digit test is applied to the *whole token*, not to a trailing character class:
# requiring a digit after the optional hyphenated group silently excluded every name
# whose digit sits inside it, which is most of them ("HX-2", "Orchid-7").
_ENTITY = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*\b")
_CAPITALIZED_NAME = re.compile(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)+\b")


def _entity_tokens(text: str) -> set[str]:
    """Entity keys in `text`, lowercased."""
    numbered = {
        m.group(0).lower() for m in _ENTITY.finditer(text or "") if any(ch.isdigit() for ch in m.group(0))
    }
    # Multi-word proper nouns are entities in their own right, and must be lowercased
    # like everything else or they never match across differently-cased sources.
    named = {m.group(0).lower() for m in _CAPITALIZED_NAME.finditer(text or "")}
    return numbered | named


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass
class PlanResult:
    topics_created: int = 0
    topics_reused: int = 0
    pages_created: int = 0
    pages_marked_dirty: int = 0
    clusters_assigned: int = 0
    clusters_uncovered: list[int] = field(default_factory=list)
    topic_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "topics_created": self.topics_created,
            "topics_reused": self.topics_reused,
            "pages_created": self.pages_created,
            "pages_marked_dirty": self.pages_marked_dirty,
            "clusters_assigned": self.clusters_assigned,
            "clusters_uncovered": len(self.clusters_uncovered),
        }


def entity_keys(claim: Claim) -> set[str]:
    """Distinctive tokens a claim contributes to topic identity."""
    return _entity_tokens(claim.statement)


def slugify(label: str) -> str:
    """A stable, lowercase, hyphenated slug. Never changes once created."""
    normalized = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode()
    slug = _SLUG_STRIP.sub("-", normalized.lower()).strip("-")
    return slug or "topic"


# An entity key must appear this many times *within each* cluster before it links two
# of them. Mere sharing is far too weak: a passing mention of "Ferro City" in an
# otherwise unrelated claim would fuse two topics.
MIN_ENTITY_MENTIONS = 2


def _cluster_entities(db: Session, cluster_id: int) -> dict[str, int]:
    """How often each entity key appears in this cluster's active claims."""
    counts: dict[str, int] = {}
    for (statement,) in db.execute(
        select(Claim.statement).where(Claim.cluster_id == cluster_id, Claim.status == "active")
    ).all():
        for key in _entity_tokens(statement or ""):
            counts[key] = counts.get(key, 0) + 1
    return counts


def _cluster_members(db: Session, cluster_id: int) -> list[Claim]:
    return list(
        db.execute(select(Claim).where(Claim.cluster_id == cluster_id, Claim.status == "active")).scalars()
    )


def _group_clusters(db: Session, clusters: list[ClaimCluster]) -> list[list[int]]:
    """Union clusters that belong together.

    Two signals, either is enough: similar centroids, or sharing an entity key that
    appears in at least two claims of each cluster ("HX-2" links the launch news to
    the lab results even when the sentences share almost no words).
    """
    limits = thresholds()
    limit = limits["topic_sim"]
    parent = {c.id: c.id for c in clusters}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    entities = {c.id: _cluster_entities(db, c.id) for c in clusters}

    # 1. Union clusters that share entities with >= MIN_ENTITY_MENTIONS
    entity_to_clusters: dict[str, list[int]] = {}
    for c in clusters:
        for k, n in entities[c.id].items():
            if n >= MIN_ENTITY_MENTIONS:
                entity_to_clusters.setdefault(k, []).append(c.id)
    for c_ids in entity_to_clusters.values():
        first = c_ids[0]
        for other in c_ids[1:]:
            union(first, other)

    # 2. Union clusters by centroid cosine similarity using pgvector
    cluster_ids = {c.id for c in clusters}
    for a in clusters:
        if a.centroid is None:
            continue
        vector = list(a.centroid)
        stmt = (
            select(ClaimCluster.id)
            .where(ClaimCluster.user_id == a.user_id)
            .where(ClaimCluster.id != a.id)
            .where(ClaimCluster.id.in_(cluster_ids))
            .where(ClaimCluster.centroid.is_not(None))
            .where(ClaimCluster.centroid.cosine_distance(vector) <= (1.0 - limit))
            .order_by(ClaimCluster.centroid.cosine_distance(vector))
            .limit(10)
        )
        for other_id in db.execute(stmt).scalars():
            union(a.id, other_id)

    groups: dict[int, list[int]] = {}
    for cluster in clusters:
        groups.setdefault(find(cluster.id), []).append(cluster.id)
    return list(groups.values())


def _label_for(db: Session, cluster_ids: list[int]) -> str:
    """Name the topic after the entity its clusters keep mentioning.

    An entity key ("HX-2", "Ferro City") names a subject; a cluster's keyword label
    ("spokesperson differ comment") names a sentence. Entities win.
    """
    keys: dict[str, int] = {}
    for cluster_id in cluster_ids:
        for key, count in _cluster_entities(db, cluster_id).items():
            keys[key] = keys.get(key, 0) + count

    repeated = {k: n for k, n in keys.items() if n >= MIN_ENTITY_MENTIONS}
    if repeated:
        # Prefer the longest entity at the top frequency: "Ferro City" over "Ferro".
        return sorted(repeated, key=lambda k: (-repeated[k], -len(k), k))[0]

    if keys:
        return sorted(keys, key=lambda k: (-keys[k], k))[0]

    best = ""
    for cluster_id in cluster_ids:
        cluster = db.get(ClaimCluster, cluster_id)
        if cluster is not None and cluster.label and len(cluster.label) > len(best):
            best = cluster.label
    return best or "untitled topic"


def _unique_slug(db: Session, user_id: uuid.UUID, base: str, topic_id: int | None) -> str:
    slug = slugify(base) or "topic"
    candidate = slug
    suffix = 2
    while True:
        existing = db.execute(
            select(Topic).where(Topic.user_id == user_id, Topic.slug == candidate)
        ).scalar_one_or_none()
        if existing is None or (topic_id is not None and existing.id == topic_id):
            return candidate
        candidate = f"{slug}-{suffix}"
        suffix += 1


def _existing_topics(db: Session, user_id: uuid.UUID) -> dict[int, set[int]]:
    """Topic id -> the cluster ids currently assigned to it."""
    out: dict[int, set[int]] = {}
    rows = db.execute(
        select(ClaimCluster.id, ClaimCluster.topic_id).where(
            ClaimCluster.user_id == user_id, ClaimCluster.topic_id.is_not(None)
        )
    ).all()
    for cluster_id, topic_id in rows:
        out.setdefault(topic_id, set()).add(cluster_id)
    return out


def plan_topics(db: Session, user_id: uuid.UUID) -> PlanResult:
    """Group clusters into topics and ensure a page per topic."""
    from app.wiki.compiler import input_hash, page_inputs

    result = PlanResult()
    clusters = list(
        db.execute(
            select(ClaimCluster).where(ClaimCluster.user_id == user_id, ClaimCluster.size > 0)
        ).scalars()
    )
    if not clusters:
        return result

    assignments = _existing_topics(db, user_id)
    for group in _group_clusters(db, clusters):
        members = [db.get(ClaimCluster, cid) for cid in group]
        members = [m for m in members if m is not None]
        claims = [c for cid in group for c in _cluster_members(db, cid)]
        documents = {c.document_id for c in claims}
        if len(claims) < MIN_CLAIMS_PER_TOPIC or len(documents) < MIN_DOCUMENTS_PER_TOPIC:
            result.clusters_uncovered.extend(group)
            continue

        overlap = max(
            (
                len(set(group) & assigned) / max(len(set(group) | assigned), 1)
                for assigned in assignments.values()
            ),
            default=0.0,
        )
        topic = None
        if overlap >= REUSE_JACCARD:
            candidate_id = max(
                assignments,
                key=lambda t: len(set(group) & assignments[t]),
            )
            topic = db.get(Topic, candidate_id)
            result.topics_reused += 1

        if topic is None:
            label = _label_for(db, group)
            topic = Topic(
                user_id=user_id,
                slug=_unique_slug(db, user_id, label, None),
                label=label,
            )
            db.add(topic)
            db.flush()
            result.topics_created += 1

        for cluster_id in group:
            cluster = db.get(ClaimCluster, cluster_id)
            if cluster is not None:
                cluster.topic_id = topic.id
                result.clusters_assigned += 1

        result.topic_ids.append(topic.id)
        _ensure_page(db, user_id, topic, result, page_inputs, input_hash)

    db.flush()
    return result


def _ensure_page(
    db: Session, user_id: uuid.UUID, topic: Topic, result: PlanResult, inputs_fn, hash_fn
) -> None:
    """Create the topic's page, or mark the existing one dirty if its inputs moved."""
    page = db.execute(
        select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.topic_id == topic.id)
    ).scalar_one_or_none()

    if page is None:
        page = WikiPage(
            user_id=user_id,
            topic_id=topic.id,
            slug=topic.slug,
            title=topic.label,
            status="dirty",
        )
        db.add(page)
        db.flush()
        result.pages_created += 1
        return

    # A slug is permanent: repoint a page whose slug drifted rather than renaming it.
    if page.slug != topic.slug:
        page.title = topic.label

    digest = hash_fn(inputs_fn(db, page), prompt_version="v1", model="heuristic")
    if digest != page.input_hash:
        page.status = "dirty"
        result.pages_marked_dirty += 1


__all__ = [
    "MIN_CLAIMS_PER_TOPIC",
    "MIN_DOCUMENTS_PER_TOPIC",
    "PAIRWISE_LIMIT",
    "REUSE_JACCARD",
    "PlanResult",
    "entity_keys",
    "plan_topics",
    "slugify",
]
