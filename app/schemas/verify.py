"""Schemas for `/v1/verify` and `/v1/ask`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VerifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    mode: str = Field(default="auto", description="auto|heuristic|llm")
    citations: dict[int, list[int]] | None = None
    k: int = Field(default=5, ge=1, le=50)


class Citation(BaseModel):
    n: int
    chunk_id: int
    document_id: uuid.UUID
    version_id: int
    title: str = ""
    url: str | None = None
    source: str = ""
    date: str | None = None
    quote: str = ""
    char_start: int
    char_end: int
    page: int | None = None
    score: float = 0.0


class SentenceVerdictOut(BaseModel):
    index: int
    text: str
    char_start: int
    char_end: int
    verdict: str
    confidence: float = 0.0
    method: str = "heuristic"
    rationale: str = ""
    cited_ok: bool | None = None
    flags: list[str] = []
    supporting: list[Citation] = []
    contradicting: list[Citation] = []


class VerifyResponse(BaseModel):
    sentences: list[SentenceVerdictOut]
    summary: dict[str, int]
    timings_ms: dict[str, float] = {}
    method: str = "heuristic"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=8, ge=1, le=30)


class AnswerSentence(BaseModel):
    index: int
    text: str
    char_start: int
    char_end: int
    verdict: str
    confidence: float = 0.0
    citations: list[int] = []
    flags: list[str] = []
    method: str = "heuristic"


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question: str
    answer_md: str
    sentences: list[AnswerSentence] = []
    citations: list[Citation] = []
    method: str = "heuristic"
    created_at: datetime | None = None


class SaveNoteRequest(BaseModel):
    title: str | None = None
    extra: dict[str, Any] = {}
