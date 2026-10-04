"""Temporal tables: watches and belief diffs.

The claim lifecycle itself lives on `claims` and `claim_events` (from M8). This module
holds only what is *about* watching and diffing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base

EMBEDDING_DIM = 384


class Watch(Base):
    """Something the user wants kept current.

    A `source` watch re-ingests a feed; a `query` watch finds claims similar to the
    stored query embedding, which is how "what changed about X?" becomes a standing
    question rather than a search someone has to remember to run.
    """

    __tablename__ = "watches"
    __table_args__ = (Index("ix_watches_user_kind", "user_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # source | query
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=True
    )
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BeliefDiff(Base):
    """One computed "what changed" report over a period.

    `payload` holds the structured sections; `digest_md` is the rendered summary a
    reader actually reads. Storing both means the report can be re-rendered without
    recomputing, and the computation stays inspectable.
    """

    __tablename__ = "belief_diffs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    scope_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    scope_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    digest_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # How the digest was written: "llm:<model>", "template", or the fallback reason.
    digest_method: Mapped[str] = mapped_column(String(120), nullable=False, default="template")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["BeliefDiff", "Watch"]
