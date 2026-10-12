"""Sources: where material comes from (webpage, rss, pdf, upload, note, clip, demo)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSONB, Base, TimestampMixin, UUIDPk

SOURCE_TYPES = (
    "webpage",
    "rss",
    "pdf",
    "file",
    "note",
    "clip",
    "conversation",
    "demo",
)

# The user's judgement of how far a source can be trusted; weights live in app/claims/camps.py.
SOURCE_RELIABILITIES = ("unknown", "low", "medium", "high")


class Source(Base, TimestampMixin):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("reliability IN ('unknown', 'low', 'medium', 'high')", name="ck_sources_reliability"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDPk, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    watch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refresh_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reliability: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )
    reliability_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    documents: Mapped[list[Document]] = relationship(  # noqa: F821
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )


__all__ = ["SOURCE_RELIABILITIES", "SOURCE_TYPES", "Source"]
