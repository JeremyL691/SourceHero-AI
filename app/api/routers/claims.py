"""Claim ledger endpoints: claims, clusters, conflicts, and relation labelling."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.claims.camps import cluster_camp_inputs, compute_camps
from app.core import time as clock
from app.core.errors import NotFoundError
from app.models.claim import Claim, ClaimCluster, ClaimEvent, ClaimRelation
from app.schemas.claims import (
    ClaimDetailOut,
    ClaimOut,
    ClusterOut,
    ConflictOut,
    RelationOut,
    RelationPatchIn,
)

router = APIRouter(tags=["claims"])


def _claims_of(user: CurrentUser) -> Select[tuple[Claim]]:
    return select(Claim).where(Claim.user_id == user.id)


@router.get("/claims", response_model=list[ClaimOut], summary="List claims")
def list_claims(
    document_id: uuid.UUID | None = None,
    cluster_id: int | None = None,
    claim_status: str | None = Query(None, alias="status"),
    claim_type: str | None = Query(None, alias="type"),
    q: str | None = None,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[ClaimOut]:
    stmt = _claims_of(user)
    if document_id:
        stmt = stmt.where(Claim.document_id == document_id)
    if cluster_id:
        stmt = stmt.where(Claim.cluster_id == cluster_id)
    if claim_status:
        stmt = stmt.where(Claim.status == claim_status)
    if claim_type:
        stmt = stmt.where(Claim.claim_type == claim_type)
    if q:
        stmt = stmt.where(Claim.statement.ilike(f"%{q}%"))
    rows = db.execute(
        stmt.order_by(Claim.asserted_at.desc(), Claim.id.desc()).limit(page.limit).offset(page.offset)
    ).scalars()
    return [ClaimOut.model_validate(c) for c in rows]


@router.get("/claims/{claim_id}", response_model=ClaimDetailOut, summary="One claim with its relations")
def get_claim(
    claim_id: int,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> ClaimDetailOut:
    claim = _claim(db, user, claim_id)
    relations = (
        db.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == user.id,
                or_(ClaimRelation.a_claim_id == claim.id, ClaimRelation.b_claim_id == claim.id),
            )
        )
        .scalars()
        .all()
    )
    events = (
        db.execute(select(ClaimEvent).where(ClaimEvent.claim_id == claim.id).order_by(ClaimEvent.at))
        .scalars()
        .all()
    )
    return ClaimDetailOut.model_validate(claim).model_copy(
        update={
            "relations": [RelationOut.model_validate(r) for r in relations],
            "events": events,
        }
    )


@router.get("/clusters", response_model=list[ClusterOut], summary="List claim clusters")
def list_clusters(
    conflicted: bool | None = None,
    topic_id: int | None = None,
    sort: str = Query("conflicts", pattern="^(conflicts|size|recent)$"),
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[ClusterOut]:
    stmt = select(ClaimCluster).where(ClaimCluster.user_id == user.id)
    if conflicted is not None:
        stmt = stmt.where(ClaimCluster.conflict_count > 0 if conflicted else ClaimCluster.conflict_count == 0)
    if topic_id is not None:
        stmt = stmt.where(ClaimCluster.topic_id == topic_id)
    order = {
        "conflicts": ClaimCluster.conflict_count.desc(),
        "size": ClaimCluster.size.desc(),
        "recent": ClaimCluster.updated_at.desc(),
    }[sort]
    rows = db.execute(stmt.order_by(order).limit(page.limit).offset(page.offset)).scalars()
    return [ClusterOut.model_validate(c) for c in rows]


@router.get("/clusters/{cluster_id}", response_model=ClusterOut, summary="One cluster with its camps")
def get_cluster(
    cluster_id: int,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> ClusterOut:
    cluster = db.get(ClaimCluster, cluster_id)
    if cluster is None or cluster.user_id != user.id:
        raise NotFoundError("cluster not found")

    claims, relations = cluster_camp_inputs(db, cluster_id)
    # claim_ids are the cluster's own active claims; the camps may also show claims from other clusters.
    ids = list(
        db.execute(select(Claim.id).where(Claim.cluster_id == cluster_id, Claim.status == "active")).scalars()
    )
    camps = compute_camps(claims, relations)
    return ClusterOut.model_validate(cluster).model_copy(
        update={"camps": _with_camp_claims(camps.as_dict(), db), "claim_ids": ids}
    )


def _with_camp_claims(payload: dict, db: Session) -> dict:
    """Resolve each camp's claim ids into full claims.

    The divergence map has to show the quote and the date, not an id: a client that
    fetched them separately would race, and the counts would not match the camp.
    """
    wanted = set(cid for camp in payload.get("camps", []) for cid in camp.get("claim_ids", []))
    for comp in payload.get("components", []):
        for cids in comp.get("camps", {}).values():
            wanted.update(cids)

    resolved: dict[int, Claim] = {}
    if wanted:
        resolved = {c.id: c for c in db.execute(select(Claim).where(Claim.id.in_(list(wanted)))).scalars()}
    out = dict(payload)
    out["camps"] = [
        {
            **camp,
            "claims": [
                _claim_summary(db, resolved[cid]) for cid in camp.get("claim_ids", []) if cid in resolved
            ],
        }
        for camp in payload.get("camps", [])
    ]
    if "components" in payload:
        out["components"] = [
            {
                **comp,
                "camps": {
                    k: [_claim_summary(db, resolved[cid]) for cid in cids if cid in resolved]
                    for k, cids in comp.get("camps", {}).items()
                },
            }
            for comp in payload.get("components", [])
        ]
    return out


def _claim_summary(db: Session, claim) -> dict:
    from app.models.document import Document
    from app.models.source import Source

    document = db.get(Document, claim.document_id) if claim.document_id else None
    source = db.get(Source, document.source_id) if document else None
    return {
        "claim_id": claim.id,
        "statement": claim.statement,
        "quote": claim.quote,
        "status": claim.status,
        "date": claim.asserted_at.date().isoformat() if claim.asserted_at else None,
        "document_id": str(claim.document_id) if claim.document_id else None,
        "title": document.title if document else "",
        "url": document.url if document else None,
        "source": source.name if source else "",
        "char_start": claim.char_start,
        "char_end": claim.char_end,
    }


@router.get("/conflicts", response_model=list[ConflictOut], summary="Unresolved contradictions")
def list_conflicts(
    cluster_id: int | None = None,
    topic_id: int | None = None,
    include_resolved: bool = False,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[ConflictOut]:
    a = Claim.__table__.alias("a")
    b = Claim.__table__.alias("b")
    stmt = (
        select(ClaimRelation)
        .join(a, a.c.id == ClaimRelation.a_claim_id)
        .join(b, b.c.id == ClaimRelation.b_claim_id)
        .where(ClaimRelation.user_id == user.id)
        .where(ClaimRelation.relation == "contradicts")
        # A rejected relation is the user's decision that it is not a conflict.
        .where(or_(ClaimRelation.user_label.is_(None), ClaimRelation.user_label != "rejected"))
    )
    if cluster_id is not None:
        stmt = stmt.where(or_(a.c.cluster_id == cluster_id, b.c.cluster_id == cluster_id))
    if topic_id is not None:
        from app.models.wiki import Topic

        topic = db.get(Topic, topic_id)
        if topic is None or topic.user_id != user.id:
            raise NotFoundError("Topic not found", code="topic_not_found")
        # A conflict belongs to a topic when either side's cluster does.
        topic_clusters = select(ClaimCluster.id).where(
            ClaimCluster.user_id == user.id, ClaimCluster.topic_id == topic_id
        )
        stmt = stmt.where(or_(a.c.cluster_id.in_(topic_clusters), b.c.cluster_id.in_(topic_clusters)))
    if not include_resolved:
        stmt = stmt.where(ClaimRelation.resolved_at.is_(None))
    stmt = stmt.order_by(ClaimRelation.confidence.desc(), ClaimRelation.id.asc())
    relations = db.execute(stmt.limit(page.limit).offset(page.offset)).scalars().all()

    from app.models.document import Document

    doc_ids: set[uuid.UUID] = set()
    for relation in relations:
        claim_a = db.get(Claim, relation.a_claim_id)
        claim_b = db.get(Claim, relation.b_claim_id)
        if claim_a is not None and claim_b is not None:
            doc_ids.add(claim_a.document_id)
            doc_ids.add(claim_b.document_id)

    docs_by_id = (
        {d.id: d for d in db.execute(select(Document).where(Document.id.in_(doc_ids))).scalars()}
        if doc_ids
        else {}
    )

    out: list[ConflictOut] = []
    for relation in relations:
        claim_a = db.get(Claim, relation.a_claim_id)
        claim_b = db.get(Claim, relation.b_claim_id)
        if claim_a is None or claim_b is None:
            continue

        doc_a = docs_by_id.get(claim_a.document_id)
        doc_b = docs_by_id.get(claim_b.document_id)

        claim_a_out = ClaimOut.model_validate(claim_a).model_copy(
            update={
                "document_title": doc_a.title if doc_a else "",
                "document_url": doc_a.url if doc_a else None,
            }
        )
        claim_b_out = ClaimOut.model_validate(claim_b).model_copy(
            update={
                "document_title": doc_b.title if doc_b else "",
                "document_url": doc_b.url if doc_b else None,
            }
        )

        out.append(
            ConflictOut(
                id=relation.id,
                a=claim_a_out,
                b=claim_b_out,
                confidence=relation.confidence,
                rationale=relation.rationale,
                cluster_id=claim_a.cluster_id or claim_b.cluster_id,
                resolved_at=relation.resolved_at,
            )
        )
    return out


@router.patch("/relations/{relation_id}", response_model=RelationOut, summary="Label a relation")
def patch_relation(
    relation_id: int,
    payload: RelationPatchIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> RelationOut:
    relation = db.get(ClaimRelation, relation_id)
    if relation is None or relation.user_id != user.id:
        raise NotFoundError("relation not found")
    relation.user_label = payload.user_label
    relation.labeled_at = clock.utcnow()
    # A rejected relation stops counting as a conflict straight away.
    if payload.user_label == "rejected":
        relation.resolved_at = clock.utcnow()
    db.commit()
    db.refresh(relation)
    return RelationOut.model_validate(relation)


@router.get("/documents/{document_id}/claims", response_model=list[ClaimOut], summary="A document's claims")
def document_claims(
    document_id: uuid.UUID,
    version_id: int | None = None,
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[ClaimOut]:
    from app.models.document import Document

    document = db.get(Document, document_id)
    if document is None or document.user_id != user.id:
        raise NotFoundError("document not found")
    stmt = select(Claim).where(Claim.user_id == user.id, Claim.document_id == document_id)
    if version_id is not None:
        stmt = stmt.where(Claim.version_id == version_id)
    rows = db.execute(stmt.order_by(Claim.char_start).offset(page.offset).limit(page.limit)).scalars()
    return [ClaimOut.model_validate(c) for c in rows]


def _claim(db: Session, user: CurrentUser, claim_id: int) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None or claim.user_id != user.id:
        raise NotFoundError("claim not found")
    return claim


__all__ = ["router"]
