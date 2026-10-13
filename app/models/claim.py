"""The claim ledger: atomic claims, their clusters, and the relations between them."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base

EMBEDDING_DIM = 384

CLAIM_STATUSES = ("active", "superseded", "retracted", "carried")
CLAIM_TYPES = ("fact", "number", "date", "prediction", "opinion")
RELATIONS = ("supports", "contradicts", "refines", "supersedes", "duplicate")


class Claim(Base):
    """One atomic, checkable assertion, carrying the verbatim quote it came from.

    The `char_start`/`char_end`/`quote` triple is an invariant, not a convention:
    `version.clean_text[char_start:char_end] == quote` always holds, because the
    quote is re-read from the source text rather than trusted from whatever produced
    it. That is what makes every citation in the product falsifiable.
    """

    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claims_user_id", "user_id"),
        # Trigram search over the statement, for the claim list's text filter.
        Index(
            "ix_claims_statement_trgm",
            "statement",
            postgresql_using="gin",
            postgresql_ops={"statement": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: re-chunking must not delete the claim itself.
    chunk_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="")
    predicate: Mapped[str] = mapped_column(Text, nullable=False, default="")
    object: Mapped[str] = mapped_column(Text, nullable=False, default="")
    qualifiers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False, default="fact")
    numbers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # -1 when the sentence is negated; 1 otherwise.
    polarity: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    # World time: when this was asserted. Drives the claim lifecycle.
    asserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # System time: when we learned it.
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    cluster_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("claim_clusters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor: Mapped[str] = mapped_column(String(120), nullable=False, default="heuristic")
    prompt_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ClaimCluster(Base):
    """A group of claims about the same thing, with a running centroid.

    The centroid is an average of member vectors, so it drifts as members arrive; it
    is normalised after every update to keep cosine comparison meaningful.
    """

    __tablename__ = "claim_clusters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    label_source: Mapped[str] = mapped_column(String(32), nullable=False, default="keywords")
    centroid: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Nullable and foreign-keyed only from 0006_wiki; declared here as a plain column.
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ClaimRelation(Base):
    """How two claims relate. Stored with `a_claim_id < b_claim_id` for idempotency."""

    __tablename__ = "claim_relations"
    __table_args__ = (Index("ix_claim_relations_pair", "user_id", "a_claim_id", "b_claim_id", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    a_claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    b_claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    # Which way a directional relation points; null for symmetric ones.
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    method: Mapped[str] = mapped_column(String(80), nullable=False, default="heuristic")
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # A user label always wins over anything the pipeline infers.
    user_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set when the contradiction stops applying (e.g. one side was superseded).
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClaimEvent(Base):
    """The bi-temporal event stream that direction B reads.

    `at` is world time (when the thing happened); `observed_at` is when we learned it.
    A snapshot backfilled today about last March is `at=2026-03`, `observed_at=today`.
    """

    __tablename__ = "claim_events"
    __table_args__ = (Index("ix_claim_events_user_at", "user_id", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    related_claim_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "CLAIM_STATUSES",
    "CLAIM_TYPES",
    "RELATIONS",
    "Claim",
    "ClaimCluster",
    "ClaimEvent",
    "ClaimRelation",
]
