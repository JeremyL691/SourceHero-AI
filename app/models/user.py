"""Identity: users and personal access tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base, TimestampMixin, UUIDPk

SCOPES_READ = "read"
SCOPES_WRITE = "write"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUIDPk, primary_key=True, default=uuid.uuid4)
    # Stored lowercased; uniqueness is what stops two accounts per address.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auth_provider: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    external_id: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True)
    # Bumped by logout-all; a JWT carries the value it was issued with and dies when it differs.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class ApiToken(Base, TimestampMixin):
    """PAT. Only the sha256 and a short prefix are stored; the plaintext is shown once."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUIDPk, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=lambda: [SCOPES_READ, SCOPES_WRITE]
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    """Instance-level settings; these override .env for the LLM subsystem."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


__all__ = ["AppSetting", "ApiToken", "SCOPES_READ", "SCOPES_WRITE", "User"]
