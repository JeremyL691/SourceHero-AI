"""Pydantic schemas for LLM structured output.

Defined here rather than inside the call sites so the JSON Schema the prompt carries
is stable and reviewable, and so `FAKE_DEFAULTS` can return exactly the same shape a
live model would.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["supported", "contradicted", "disputed", "unsupported"]


# --- verify (prompt "verify") -------------------------------------------


class VerifyItem(BaseModel):
    index: int = Field(description="1-based position of the sentence in the input")
    text: str
    evidence: list[dict] = Field(default_factory=list)


class VerifyResultItem(BaseModel):
    sentence_index: int
    verdict: Verdict
    supporting_ids: list[int] = Field(default_factory=list)
    contradicting_ids: list[int] = Field(default_factory=list)
    confidence: float = 0.5
    rationale: str = ""


class VerifyResponse(BaseModel):
    results: list[VerifyResultItem] = Field(default_factory=list)


# --- claim extraction (prompt "claim_extract") ---------------------------


class ClaimNumber(BaseModel):
    value: float
    unit: str
    text: str


class ClaimItem(BaseModel):
    statement: str
    quote: str
    subject: str = ""
    predicate: str = ""
    object: str = ""
    qualifiers: dict = Field(default_factory=dict)
    claim_type: Literal["fact", "number", "date", "prediction", "opinion"] = "fact"
    numbers: list[ClaimNumber] = Field(default_factory=list)
    confidence: float = 0.5


class ClaimExtraction(BaseModel):
    claims: list[ClaimItem] = Field(default_factory=list)


# --- relation judging (prompt "relation_judge") -------------------------


class RelationResult(BaseModel):
    pair_index: int
    relation: Literal["supports", "contradicts", "supersedes", "refines", "unrelated"]
    direction: Literal["a_to_b", "b_to_a"] | None = None
    confidence: float = 0.5
    rationale: str = ""


class RelationResponse(BaseModel):
    results: list[RelationResult] = Field(default_factory=list)


# --- topic naming (prompt "topic_label") --------------------------------


class TopicLabel(BaseModel):
    label: str


# --- wiki (prompts "wiki_compile" / "wiki_patch") -----------------------


class WikiSentence(BaseModel):
    text: str
    claim_ids: list[int] = Field(default_factory=list)


class WikiSection(BaseModel):
    id: str = Field(description="stable kebab-case slug")
    heading: str
    kind: Literal["overview", "consensus", "disputed", "history", "details"] = "overview"
    sentences: list[WikiSentence] = Field(default_factory=list)


class WikiBody(BaseModel):
    title: str
    summary: list[WikiSentence] = Field(default_factory=list)
    sections: list[WikiSection] = Field(default_factory=list)
    see_also: list[str] = Field(default_factory=list)


class WikiPatch(BaseModel):
    updated_sections: list[WikiSection] = Field(default_factory=list)
    new_sections: list[WikiSection] = Field(default_factory=list)
    removed_section_ids: list[str] = Field(default_factory=list)


# --- belief-diff digest (prompt "diff_digest") --------------------------


class DigestBullet(BaseModel):
    section: Literal["updated", "conflicts", "stale_notes", "new"]
    text: str
    claim_ids: list[int] = Field(default_factory=list)


class DiffDigest(BaseModel):
    bullets: list[DigestBullet] = Field(default_factory=list)


__all__ = [
    "ClaimExtraction",
    "ClaimItem",
    "ClaimNumber",
    "DiffDigest",
    "DigestBullet",
    "RelationResponse",
    "RelationResult",
    "TopicLabel",
    "Verdict",
    "VerifyItem",
    "VerifyResponse",
    "VerifyResultItem",
    "WikiBody",
    "WikiPatch",
    "WikiSection",
    "WikiSentence",
]
