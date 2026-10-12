"""The wiki: topics, pages, revisions, the page↔claim join, links and lint findings.

A page is *compiled* from claims, not written. Every sentence carries the claim ids it
came from, so a reader can check any statement back to its source, and a change to a
claim invalidates exactly the pages that cited it rather than all of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base

EMBEDDING_DIM = 384

PAGE_STATUSES = ("dirty", "compiling", "clean", "failed")


def _empty_dict() -> dict:
    """A fresh dict per row.

    `default=dict` would hand every instance the *same* dict object, so one page's
    body would alias another's before either was flushed.
    """
    return {}


class Topic(Base):
    """A group of clusters about the same subject; one wiki page hangs off it."""

    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_topics_user_slug"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    centroid: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WikiPage(Base):
    """One compiled page.

    `input_hash` is the build-system part: it covers the prompt version, the model and
    the exact set of claims that fed the page. When it changes the page is dirty, and
    only then is it recompiled.
    """

    __tablename__ = "wiki_pages"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_wiki_pages_user_slug"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="dirty")
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=_empty_dict)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    compiled_input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=_empty_dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    compiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    compile_method: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WikiPageRevision(Base):
    """A stored version of a page, so a reader can see what changed and why."""

    __tablename__ = "wiki_page_revisions"
    __table_args__ = (UniqueConstraint("page_id", "revision", name="uq_wiki_revisions"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=_empty_dict)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    compile_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WikiPageClaim(Base):
    """Which claims a page was compiled from -- the provenance index."""

    __tablename__ = "wiki_page_claims"
    __table_args__ = (Index("ix_wiki_page_claims_claim", "claim_id"),)

    page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True
    )
    claim_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )


class WikiLink(Base):
    """A directed link between two pages."""

    __tablename__ = "wiki_links"

    from_page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True
    )
    to_page_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True
    )
    anchor: Mapped[str] = mapped_column(Text, nullable=False, default="")


class LintFinding(Base):
    """A problem with a page. `resolved_at` is set once the cause is gone."""

    __tablename__ = "lint_findings"
    __table_args__ = (Index("ix_lint_findings_user_severity", "user_id", "severity"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    page_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=_empty_dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "PAGE_STATUSES",
    "LintFinding",
    "Topic",
    "WikiLink",
    "WikiPage",
    "WikiPageClaim",
    "WikiPageRevision",
]
