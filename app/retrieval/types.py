"""Retrieval types shared by the API, the tools and the evals."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SearchMode = Literal["hybrid", "lexical", "vector"]


class SearchFilters(BaseModel):
    """Narrowing applied to both the lexical and the vector leg.

    Defaults follow the plan: only the document's *current* version is searched, so
    a superseded sentence cannot be cited as present-tense fact. Callers that want the
    history (the belief-diff views, for instance) opt in explicitly.
    """

    source_ids: list[uuid.UUID] | None = None
    source_types: list[str] | None = None
    collection_id: int | None = None
    tag_ids: list[int] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    include_old_versions: bool = False


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=50)
    mode: SearchMode = "hybrid"
    rerank: bool | None = None
    filters: SearchFilters | None = None


class HitOut(BaseModel):
    chunk_id: int
    document_id: uuid.UUID
    version_id: int
    title: str = ""
    url: str | None = None
    source_id: uuid.UUID
    source_name: str = ""
    source_type: str = ""
    published_at: datetime | None = None
    text: str
    char_start: int
    char_end: int
    page: int | None = None
    snippet: str
    scores: dict = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    hits: list[HitOut]
    mode_effective: str
    timings_ms: dict[str, float] = Field(default_factory=dict)
    total_candidates: int = 0


@dataclass
class Hit:
    chunk_id: int
    document_id: uuid.UUID
    version_id: int
    title: str
    url: str | None
    source_id: uuid.UUID
    source_name: str
    source_type: str
    published_at: datetime | None
    text: str
    char_start: int
    char_end: int
    page: int | None
    snippet: str
    scores: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    hits: list[Hit]
    mode_effective: str
    timings_ms: dict[str, float] = field(default_factory=dict)
    total_candidates: int = 0


__all__ = [
    "Hit",
    "HitOut",
    "SearchFilters",
    "SearchMode",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
]
