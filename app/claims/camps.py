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

from dataclasses import dataclass, field
from datetime import datetime

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
class Camps:
    camps: list[Camp] = field(default_factory=list)
    unaligned: list[int] = field(default_factory=list)
    balanced: bool = True
    frustrated_edges: list[tuple[int, int]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "camps": [c.as_dict() for c in self.camps],
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
        kind = relation.user_label or relation.relation
        if kind in AGREEING:
            sign = 1
        elif kind in OPPOSING:
            sign = -1
        else:
            continue
        out.append((relation.a_claim_id, relation.b_claim_id, sign))
    return out


def compute_camps(claims: list[Claim], relations: list[ClaimRelation]) -> Camps:
    """Partition a cluster's active claims into opposing camps."""
    by_id = {c.id: c for c in claims}
    edges = _edges(relations)

    adjacency: dict[int, list[tuple[int, int]]] = {}
    for a, b, sign in edges:
        adjacency.setdefault(a, []).append((b, sign))
        adjacency.setdefault(b, []).append((a, sign))

    colour: dict[int, int] = {}
    frustrated: list[tuple[int, int]] = []
    unaligned: list[int] = []

    for node in by_id:
        if node in colour:
            continue
        if not adjacency.get(node):
            # Nothing links it to anything; it cannot be placed on either side.
            unaligned.append(node)
            continue

        # Colours are +1 / -1 so that `colour * sign` negates properly: with {0, 1},
        # 0 * -1 == 0 and every opposing claim would land in the same camp.
        colour[node] = 1
        queue = [node]
        while queue:
            current = queue.pop()
            for neighbour, sign in adjacency.get(current, ()):
                expected = colour[current] * sign
                if neighbour in colour:
                    if colour[neighbour] != expected:
                        frustrated.append(tuple(sorted((current, neighbour))))
                else:
                    colour[neighbour] = expected
                    queue.append(neighbour)

    camps: list[Camp] = []
    grouped: dict[int, list[int]] = {}
    for node, value in colour.items():
        grouped.setdefault(value, []).append(node)

    # Order the camps so "A" is deterministic across runs.
    ordered = sorted(grouped, key=lambda v: (v != 1, sorted(grouped[v])[:1]))
    for index, value in enumerate(ordered):
        members = sorted(grouped[value])
        camp = _build_camp(by_id, members, "A" if index == 0 else "B")
        camps.append(camp)

    return Camps(
        camps=camps,
        unaligned=sorted(unaligned),
        balanced=not frustrated,
        frustrated_edges=frustrated,
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
        documents.add(claim.document_id)
        timestamps.append(claim.asserted_at)

    if timestamps:
        camp.earliest = min(timestamps)
        camp.latest = max(timestamps)
    camp.documents = sorted(documents)

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


__all__ = ["AGREEING", "Camp", "Camps", "OPPOSING", "compute_camps"]
