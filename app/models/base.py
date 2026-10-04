"""Declarative base, mixins, and shared column types."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Used by every tenant-scoped table so we never forget the index.
UUIDPk = PgUUID(as_uuid=True)


class Base(DeclarativeBase):
    """Every model inherits from this so Alembic's autogenerate sees them all."""

    type_annotation_map = {dict[str, Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserMixin:
    """Every business table carries a tenant column and an index on it."""

    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)


__all__ = [
    "JSONB",
    "TSVECTOR",
    "Base",
    "TimestampMixin",
    "UUIDPk",
    "UserMixin",
]
