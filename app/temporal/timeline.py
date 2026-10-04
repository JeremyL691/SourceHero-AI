"""The timeline: a chronological read of what happened to claims.

Bi-temporal by default. `axis=world` orders by when things happened; `axis=observed`
orders by when the system learned, which is the honest framing when a Wayback backfill
run today inserts a snapshot from March.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claim import Claim, ClaimCluster, ClaimEvent
from app.models.document import Document

EVENT_LABELS = {
    "asserted": "asserted",
    "superseded": "superseded",
    "retracted": "retracted",
    "conflict_opened": "conflict opened",
    "conflict_resolved": "conflict resolved",
}


@dataclass
class TimelineEntry:
    at: datetime
    observed_at: datetime
    event: str
    label: str
    claim_id: int
    statement: str
    document_id: uuid.UUID | None
    document_title: str = ""
    cluster_id: int | None = None
    related_claim_id: int | None = None
    related_statement: str | None = None

    def as_dict(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "event": self.event,
            "label": self.label,
            "claim_id": self.claim_id,
            "statement": self.statement,
            "document_id": str(self.document_id) if self.document_id else None,
            "document_title": self.document_title,
            "cluster_id": self.cluster_id,
            "related_claim_id": self.related_claim_id,
            "related_statement": self.related_statement,
        }


def timeline(
    db: Session,
    user_id: uuid.UUID,
    *,
    cluster_id: int | None = None,
    topic_id: int | None = None,
    document_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    axis: str = "world",
    limit: int = 200,
) -> list[TimelineEntry]:
    """Events for a scope, oldest first."""
    stmt = select(ClaimEvent).where(ClaimEvent.user_id == user_id)
    column = ClaimEvent.observed_at if axis == "observed" else ClaimEvent.at
    if start is not None:
        stmt = stmt.where(column >= start)
    if end is not None:
        stmt = stmt.where(column < end)
    events = list(db.execute(stmt.order_by(column, ClaimEvent.id).limit(limit)).scalars().all())
    if not events:
        return []

    claims = {cid: db.get(Claim, cid) for cid in {e.claim_id for e in events} if cid is not None}
    claims = {cid: c for cid, c in claims.items() if c is not None}

    allowed = _filter_claims(db, claims, cluster_id, topic_id, document_id)

    titles: dict[uuid.UUID, str] = {}
    document_ids = {c.document_id for c in allowed.values()}
    for doc_id in document_ids:
        document = db.get(Document, doc_id)
        if document is not None:
            titles[doc_id] = document.title or ""

    out: list[TimelineEntry] = []
    for event in events:
        claim = claims.get(event.claim_id)
        if claim is None or claim.id not in allowed:
            continue
        related = claims.get(event.related_claim_id) if event.related_claim_id else None
        if event.related_claim_id is not None and related is None:
            related = db.get(Claim, event.related_claim_id)
        out.append(
            TimelineEntry(
                at=event.at,
                observed_at=event.observed_at,
                event=event.event,
                label=EVENT_LABELS.get(event.event, event.event),
                claim_id=claim.id,
                statement=claim.statement,
                document_id=claim.document_id,
                document_title=titles.get(claim.document_id, ""),
                cluster_id=claim.cluster_id,
                related_claim_id=event.related_claim_id,
                related_statement=related.statement if related is not None else None,
            )
        )
    return out


def _filter_claims(
    db: Session,
    claims: dict[int, Claim],
    cluster_id: int | None,
    topic_id: int | None,
    document_id: uuid.UUID | None,
) -> dict[int, Claim]:
    allowed = dict(claims)
    if cluster_id is not None:
        allowed = {cid: c for cid, c in allowed.items() if c.cluster_id == cluster_id}
    if topic_id is not None:
        topic_clusters = set(
            db.execute(select(ClaimCluster.id).where(ClaimCluster.topic_id == topic_id)).scalars().all()
        )
        allowed = {cid: c for cid, c in allowed.items() if c.cluster_id in topic_clusters}
    if document_id is not None:
        allowed = {cid: c for cid, c in allowed.items() if c.document_id == document_id}
    return allowed


__all__ = ["EVENT_LABELS", "TimelineEntry", "timeline"]
