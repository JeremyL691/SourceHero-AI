"""Source and document schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.source import SOURCE_TYPES


class SourceOut(BaseModel):
    id: uuid.UUID
    source_type: str
    name: str
    url: str | None = None
    canonical_url: str | None = None
    status: str = "active"
    watch: bool = False
    refresh_interval_minutes: int | None = None
    next_refresh_at: datetime | None = None
    last_ingested_at: datetime | None = None
    created_at: datetime | None = None
    document_count: int = 0

    model_config = {"from_attributes": True}


class SourceCreateIn(BaseModel):
    source_type: str = Field(description="|".join(SOURCE_TYPES))
    name: str
    url: str | None = None
    config: dict = Field(default_factory=dict)
    watch: bool = False
    refresh_interval_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)


class SourceUpdateIn(BaseModel):
    name: str | None = None
    url: str | None = None
    config: dict | None = None
    status: str | None = None
    watch: bool | None = None
    refresh_interval_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)


class IngestionRunOut(BaseModel):
    id: int
    source_id: uuid.UUID | None = None
    job_id: int | None = None
    status: str
    documents_found: int = 0
    documents_new: int = 0
    documents_changed: int = 0
    documents_unchanged: int = 0
    chunks_inserted: int = 0
    error: str | None = None
    warnings: list = []
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    identity_key: str
    title: str = ""
    url: str | None = None
    canonical_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    lang: str | None = None
    current_version_id: int | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentDetailOut(DocumentOut):
    text: str = ""
    chunk_count: int = 0
    version_count: int = 0


class VersionOut(BaseModel):
    id: int
    document_id: uuid.UUID
    version_no: int
    content_hash: str
    clean_text: str = ""
    page_map: list | None = None
    origin: str
    effective_at: datetime
    fetched_at: datetime | None = None
    archived_at: datetime | None = None
    diff_stats: dict | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


__all__ = [
    "DocumentDetailOut",
    "DocumentOut",
    "IngestionRunOut",
    "SourceCreateIn",
    "SourceOut",
    "SourceUpdateIn",
    "VersionOut",
]
