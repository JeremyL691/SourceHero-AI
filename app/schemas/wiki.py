"""Wiki schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TopicOut(BaseModel):
    id: int
    slug: str
    label: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class PageOut(BaseModel):
    id: int
    topic_id: int
    slug: str
    title: str
    status: str = "dirty"
    revision: int = 0
    compiled_at: datetime | None = None
    compile_method: str = ""
    error: str | None = None
    sentence_count: int = 0
    lint_count: int = 0

    model_config = {"from_attributes": True}


class FootnoteOut(BaseModel):
    n: int
    claim_id: int
    quote: str = ""
    document_title: str = ""
    url: str | None = None
    char_start: int | None = None
    char_end: int | None = None

    model_config = {"from_attributes": True}


class PageDetailOut(PageOut):
    body: dict = {}
    body_md: str = ""
    claim_ids: list[int] = []
    backlinks: list[str] = []
    footnotes: list[FootnoteOut] = []


class RevisionOut(BaseModel):
    revision: int
    change_summary: str = ""
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class LintFindingOut(BaseModel):
    """One lint finding.

    `id` is optional because a report contains findings that have just been detected
    and not yet written -- requiring an id here made the endpoint 500 on the common
    path, which is exactly when someone is looking at the lint panel.
    """

    id: int | None = None
    page_id: int | None = None
    kind: str
    severity: str
    detail: dict = {}
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class LintReport(BaseModel):
    pages: int = 0
    findings: int = 0
    by_severity: dict = {}
    items: list[LintFindingOut] = []


class PlanRequest(BaseModel):
    force: bool = False


__all__ = [
    "FootnoteOut",
    "LintFindingOut",
    "LintReport",
    "PageDetailOut",
    "PageOut",
    "PlanRequest",
    "RevisionOut",
    "TopicOut",
]
