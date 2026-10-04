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


class PageDetailOut(PageOut):
    body: dict = {}
    body_md: str = ""
    claim_ids: list[int] = []
    backlinks: list[str] = []


class RevisionOut(BaseModel):
    revision: int
    change_summary: str = ""
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class LintFindingOut(BaseModel):
    id: int
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
    "LintFindingOut",
    "LintReport",
    "PageDetailOut",
    "PageOut",
    "PlanRequest",
    "RevisionOut",
    "TopicOut",
]
