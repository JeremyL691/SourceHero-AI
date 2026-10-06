"""Camps: two-colouring a signed graph of claims.

Each claim is a vertex. `supports`/`duplicate`/`refines` edges say "these agree"
(+1); `contradicts` edges say "these cannot both be true" (-1). Two-colouring each
connected component turns that into an A/B split, which is exactly the question a
reader asks about a disagreement: *who says what*.

A component is *unbalanced* when some edge's sign contradicts the colouring its path
implies -- in practice, three sources each contradicting the other two. That is not a
bug to hide: a triangle of pairwise contradictions means at least one of the three is
wrong, and no two-way split can represent it. The frustrated edges are reported so the
UI can say so rather than inventing a false binary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.claim import Claim, ClaimRelation

AGREEING = ("supports", "duplicate", "refines")
OPPOSING = ("contradicts",)


@dataclass
class Camp:
    label: str
    claim_ids: list[int] = field(default_factory=list)
    representative_id: int | None = None
    documents: list = field(default_factory=list)
    earliest: datetime | None = None
    latest: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "claim_ids": list(self.claim_ids),
            "representative_id": self.representative_id,
            "documents": [str(d) for d in self.documents],
            "earliest": self.earliest.isoformat() if self.earliest else None,
            "latest": self.latest.isoformat() if self.latest else None,
        }


@dataclass
class CampComponent:
    camps: dict[str, list[int]] = field(default_factory=dict)
    balanced: bool = True
    frustrated_edges: list[tuple[int, int]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "camps": {k: list(v) for k, v in self.camps.items()},
            "balanced": self.balanced,
            "frustrated_edges": [list(e) for e in self.frustrated_edges],
        }


@dataclass
class Camps:
    camps: list[Camp] = field(default_factory=list)
    components: list[CampComponent] = field(default_factory=list)
    unaligned: list[int] = field(default_factory=list)
    balanced: bool = True
    frustrated_edges: list[tuple[int, int]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "camps": [c.as_dict() for c in self.camps],
            "components": [c.as_dict() for c in self.components],
            "unaligned": list(self.unaligned),
            "balanced": self.balanced,
            "frustrated_edges": [list(e) for e in self.frustrated_edges],
        }


def _edges(relations: list[ClaimRelation]) -> list[tuple[int, int, int]]:
    """(a, b, sign) with sign +1 for agreeing and -1 for opposing."""
    out: list[tuple[int, int, int]] = []
    for relation in relations:
        if relation.resolved_at is not None:
            continue
        if relation.user_label == "rejected":
            continue
        # User label 'confirmed' preserves the underlying relation kind
        kind = (
            relation.relation
            if relation.user_label == "confirmed"
            else (relation.user_label or relation.relation)
        )
        if kind in AGREEING:
            sign = 1
        elif kind in OPPOSING:
            sign = -1
        else:
            continue
        out.append((relation.a_claim_id, relation.b_claim_id, sign))
    return out


def compute_camps(claims: list[Claim], relations: list[ClaimRelation]) -> Camps:
    """Partition a cluster's active claims into opposing camps by connected component."""
    by_id = {c.id: c for c in claims}
    edges = _edges(relations)

    adjacency: dict[int, list[tuple[int, int]]] = {}
    for a, b, sign in edges:
        if a in by_id and b in by_id:
            adjacency.setdefault(a, []).append((b, sign))
            adjacency.setdefault(b, []).append((a, sign))

    unaligned: list[int] = []
    visited: set[int] = set()
    components: list[CampComponent] = []
    flat_camps: list[Camp] = []
    all_frustrated_edges: set[tuple[int, int]] = set()

    for node in sorted(by_id.keys()):
        if not adjacency.get(node):
            unaligned.append(node)
            continue
        if node in visited:
            continue

        # BFS to discover all nodes in this connected component
        comp_nodes: list[int] = []
        queue = [node]
        visited.add(node)
        while queue:
            curr = queue.pop(0)
            comp_nodes.append(curr)
            for nbr, _ in adjacency.get(curr, ()):
                if nbr not in visited and nbr in by_id:
                    visited.add(nbr)
                    queue.append(nbr)

        # Two-colour this connected component
        colour: dict[int, int] = {node: 1}
        comp_frustrated: set[tuple[int, int]] = set()
        color_queue = [node]
        while color_queue:
            curr = color_queue.pop(0)
            for nbr, sign in adjacency.get(curr, ()):
                if nbr not in by_id:
                    continue
                expected = colour[curr] * sign
                if nbr in colour:
                    if colour[nbr] != expected:
                        comp_frustrated.add(tuple(sorted((curr, nbr))))
                else:
                    colour[nbr] = expected
                    color_queue.append(nbr)

        all_frustrated_edges.update(comp_frustrated)

        grouped: dict[int, list[int]] = {}
        for n in comp_nodes:
            grouped.setdefault(colour.get(n, 1), []).append(n)

        # Order so +1 is first
        ordered = sorted(grouped.keys(), key=lambda v: (v != 1, sorted(grouped[v])[:1]))
        members_a = sorted(grouped.get(ordered[0], [])) if len(ordered) > 0 else []
        members_b = sorted(grouped.get(ordered[1], [])) if len(ordered) > 1 else []

        camps_dict: dict[str, list[int]] = {}
        if members_a:
            camps_dict["A"] = members_a
        if members_b:
            camps_dict["B"] = members_b

        is_balanced = len(comp_frustrated) == 0
        comp_frustrated_list = sorted(list(comp_frustrated))
        components.append(
            CampComponent(
                camps=camps_dict,
                balanced=is_balanced,
                frustrated_edges=comp_frustrated_list,
            )
        )

        # Populate flat_camps for backward compatibility
        camp_offset = len(flat_camps)
        label_a = chr(ord("A") + camp_offset) if camp_offset < 26 else f"C{camp_offset}"
        label_b = chr(ord("A") + camp_offset + 1) if camp_offset + 1 < 26 else f"C{camp_offset + 1}"
        if members_a:
            flat_camps.append(_build_camp(by_id, members_a, label_a))
        if members_b:
            flat_camps.append(_build_camp(by_id, members_b, label_b))

    all_frustrated_list = sorted(list(all_frustrated_edges))
    return Camps(
        camps=flat_camps,
        components=components,
        unaligned=sorted(unaligned),
        balanced=len(all_frustrated_list) == 0,
        frustrated_edges=all_frustrated_list,
    )


def _build_camp(by_id: dict[int, Claim], ids: list[int], label: str) -> Camp:
    """A camp, with the best-connected claim standing in for the rest."""
    camp = Camp(label=label, claim_ids=list(ids))
    documents: set = set()
    timestamps: list[datetime] = []
    for claim_id in ids:
        claim = by_id.get(claim_id)
        if claim is None:
            continue
        if claim.document_id:
            documents.add(claim.document_id)
        if claim.asserted_at is not None:
            timestamps.append(claim.asserted_at)

    if timestamps:
        camp.earliest = min(timestamps)
        camp.latest = max(timestamps)
    camp.documents = sorted(documents, key=str)

    # The representative is the claim with the most distinct source documents: it
    # speaks for the camp better than any single-source claim would.
    per_document: dict = {}
    for claim_id in ids:
        claim = by_id.get(claim_id)
        if claim is not None:
            per_document[claim.document_id] = per_document.get(claim.document_id, 0) + 1
    if per_document:
        best_document = max(per_document, key=lambda d: (per_document[d], str(d)))
        camp.representative_id = next(
            (i for i in ids if by_id[i] is not None and by_id[i].document_id == best_document), None
        )
    return camp


def conflicts_for_chunks(
    db: Session,
    user_id: uuid.UUID,
    chunk_ids: list[int],
) -> list[dict]:
    """Find active conflicts (contradictions) associated with the given chunks.

    A conflict is associated if:
    1. A claim in the chunk is directly party to the contradiction relation.
    2. A claim in the chunk belongs to a cluster that has an active contradiction.
    """
    if not chunk_ids:
        return []

    from sqlalchemy import or_, select
    from sqlalchemy.orm import aliased

    from app.models.claim import Claim, ClaimRelation

    claims = list(
        db.execute(select(Claim).where(Claim.user_id == user_id, Claim.chunk_id.in_(chunk_ids)))
        .scalars()
        .all()
    )
    if not claims:
        return []

    claim_ids = {c.id for c in claims}
    cluster_ids = {c.cluster_id for c in claims if c.cluster_id is not None}

    a_claim = aliased(Claim)
    b_claim = aliased(Claim)

    conds = [
        ClaimRelation.a_claim_id.in_(claim_ids),
        ClaimRelation.b_claim_id.in_(claim_ids),
    ]
    if cluster_ids:
        conds.append(a_claim.cluster_id.in_(cluster_ids))
        conds.append(b_claim.cluster_id.in_(cluster_ids))

    stmt = (
        select(ClaimRelation, a_claim, b_claim)
        .join(a_claim, a_claim.id == ClaimRelation.a_claim_id)
        .join(b_claim, b_claim.id == ClaimRelation.b_claim_id)
        .where(
            ClaimRelation.user_id == user_id,
            ClaimRelation.relation == "contradicts",
            ClaimRelation.resolved_at.is_(None),
            or_(ClaimRelation.user_label.is_(None), ClaimRelation.user_label != "rejected"),
            or_(*conds),
        )
        .order_by(ClaimRelation.confidence.desc(), ClaimRelation.id.asc())
    )
    rows = db.execute(stmt).all()

    seen_ids: set[int] = set()
    disputes: list[dict] = []
    for rel, c_a, c_b in rows:
        if rel.id in seen_ids:
            continue
        seen_ids.add(rel.id)
        disputes.append(
            {
                "id": rel.id,
                "label": "disputed",
                "a": c_a.statement,
                "b": c_b.statement,
                "claim_a_id": c_a.id,
                "claim_b_id": c_b.id,
                "confidence": round(rel.confidence, 4),
                "cluster_id": c_a.cluster_id or c_b.cluster_id,
            }
        )
    return disputes


find_conflicts_for_chunks = conflicts_for_chunks


__all__ = [
    "AGREEING",
    "Camp",
    "CampComponent",
    "Camps",
    "OPPOSING",
    "compute_camps",
    "conflicts_for_chunks",
    "find_conflicts_for_chunks",
]
