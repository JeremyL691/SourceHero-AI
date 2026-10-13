"""Schemas for the temporal endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WatchOut(BaseModel):
    id: uuid.UUID
    kind: str
    source_id: uuid.UUID | None = None
    query: str | None = None
    created_at: datetime | None = None
    last_checked_at: datetime | None = None

    model_config = {"from_attributes": True}


class WatchCreateIn(BaseModel):
    kind: str = Field(description="source|query")
    source_id: uuid.UUID | None = None
    query: str | None = None
    refresh_interval_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 30)


class TimelineEntryOut(BaseModel):
    at: datetime
    observed_at: datetime
    event: str
    label: str
    claim_id: int
    statement: str
    document_id: uuid.UUID | None = None
    document_title: str = ""
    cluster_id: int | None = None
    related_claim_id: int | None = None
    related_statement: str | None = None


class StaleNoteOut(BaseModel):
    claim_id: int
    statement: str
    document_id: uuid.UUID
    document_title: str = ""
    reasons: list[str] = []
    asserted_at: datetime | None = None
    evidence: list[dict] = []


class DiffCreateIn(BaseModel):
    start: datetime
    end: datetime
    scope_kind: str = Field(default="all", description="all|topic|watch|cluster")
    scope_id: int | uuid.UUID | None = None
    time_axis: str = Field(default="world", description="world|observed")


class DiffOut(BaseModel):
    id: uuid.UUID
    scope_kind: str
    scope_id: int | uuid.UUID | None = None
    period_start: datetime
    period_end: datetime
    payload: dict = {}
    digest_md: str = ""
    digest_method: str = "template"
    stats: dict = {}
    created_at: datetime | None = None
    # Set when the diff was queued rather than computed inline; poll this job and
    # then read `GET /v1/diffs` for the stored result.
    job_id: int | None = None

    model_config = {"from_attributes": True}


__all__ = [
    "DiffCreateIn",
    "DiffOut",
    "StaleNoteOut",
    "TimelineEntryOut",
    "WatchCreateIn",
    "WatchOut",
]
