"""Claim ledger schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ClaimOut(BaseModel):
    id: int
    document_id: uuid.UUID
    version_id: int
    chunk_id: int | None = None
    statement: str
    quote: str
    char_start: int
    char_end: int
    subject: str = ""
    claim_type: str = "fact"
    numbers: list = []
    polarity: int = 1
    asserted_at: datetime
    valid_to: datetime | None = None
    status: str = "active"
    superseded_by: int | None = None
    cluster_id: int | None = None
    confidence: float = 0.5
    extractor: str = "heuristic"
    document_title: str = ""
    document_url: str | None = None

    model_config = {"from_attributes": True}


class RelationOut(BaseModel):
    id: int
    a_claim_id: int
    b_claim_id: int
    relation: str
    direction: str | None = None
    confidence: float = 0.5
    method: str = "heuristic"
    rationale: str = ""
    signals: dict = {}
    user_label: str | None = None
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: int
    claim_id: int
    event: str
    at: datetime
    observed_at: datetime
    related_claim_id: int | None = None

    model_config = {"from_attributes": True}


class ClaimDetailOut(ClaimOut):
    relations: list[RelationOut] = []
    events: list[EventOut] = []


class ClusterOut(BaseModel):
    id: int
    label: str = ""
    label_source: str = "keywords"
    size: int = 0
    doc_count: int = 0
    conflict_count: int = 0
    topic_id: int | None = None
    # Only present on the single-cluster route.
    camps: dict = {}
    claim_ids: list[int] = []

    model_config = {"from_attributes": True}


class ConflictOut(BaseModel):
    id: int
    a: ClaimOut
    b: ClaimOut
    confidence: float = 0.5
    rationale: str = ""
    cluster_id: int | None = None
    resolved_at: datetime | None = None


class RelationPatchIn(BaseModel):
    user_label: str = Field(
        description="confirmed|rejected|supports|contradicts|refines|supersedes|unrelated"
    )


__all__ = [
    "ClaimDetailOut",
    "ClaimOut",
    "ClusterOut",
    "ConflictOut",
    "EventOut",
    "RelationOut",
    "RelationPatchIn",
]
