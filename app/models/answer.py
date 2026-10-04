"""Persisted answers and verification results."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base


class Answer(Base):
    """One stored answer, with its per-sentence verdicts and the evidence it used.

    `method` records how it was produced, including any heuristic fallback:
    `fake`, `llm:<model>`, or `heuristic(fallback:<code>)`. The UI shows it, so a
    degraded answer is never presented as if a model had written it.
    """

    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Per-sentence verdicts, each with its own offsets and supporting/contradicting ids.
    sentences: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The evidence list the answer was allowed to cite, keyed by its citation number.
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # What retrieval produced, for the "where did this come from" panel.
    retrieval: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    method: Mapped[str] = mapped_column(String(120), nullable=False, default="heuristic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Verification(Base):
    """One `/v1/verify` call, kept so verdicts can be audited after the fact."""

    __tablename__ = "verifications"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    method: Mapped[str] = mapped_column(String(120), nullable=False, default="heuristic")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["Answer", "Verification"]
