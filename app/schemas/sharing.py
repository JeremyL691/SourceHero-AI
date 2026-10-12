"""Share link request/response shapes, and the sanitized public snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ShareCreate(BaseModel):
    kind: Literal["wiki_page", "answer"]
    target_id: str = Field(min_length=1, max_length=64)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ShareOut(BaseModel):
    id: int
    kind: str
    target_id: str
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


class ShareCreated(ShareOut):
    # Shown exactly once: the server keeps only the hash.
    token: str


class PublicFootnote(BaseModel):
    n: int
    quote: str = ""
    document_title: str = ""
    url: str | None = None


class PublicSentence(BaseModel):
    text: str
    verdict: str = ""
    citations: list[int] = []


class PublicSection(BaseModel):
    heading: str
    sentences: list[PublicSentence] = []


class PublicWikiShare(BaseModel):
    kind: Literal["wiki_page"] = "wiki_page"
    slug: str
    title: str
    summary: list[PublicSentence] = []
    sections: list[PublicSection] = []
    footnotes: list[PublicFootnote] = []


class PublicCitation(BaseModel):
    n: int
    title: str = ""
    url: str | None = None
    date: str | None = None
    quote: str = ""


class PublicAnswerShare(BaseModel):
    kind: Literal["answer"] = "answer"
    question: str
    answer_md: str = ""
    sentences: list[PublicSentence] = []
    citations: list[PublicCitation] = []


PublicShare = PublicWikiShare | PublicAnswerShare
