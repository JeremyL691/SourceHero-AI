"""Heuristic claim extraction.

The filter here is what decides whether a sentence becomes a citable, checkable claim.
It is deliberately conservative: a false claim in the ledger is worse than a missing
one, because a missing claim is invisible while a false one will be compared against
other sources and produce a conflict nobody can explain.

Length thresholds are asymmetric by script because the units differ: "Orchid-7 has 7
billion parameters." is a real claim with only three content tokens, so the English
minimum has to admit it, while a three-character Chinese sentence is noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.claims.numbers import extract_quantities
from app.claims.signals import NEGATIONS, _words, content_tokens
from app.core.logging import get_logger
from app.ingestion.segment import split_sentences

log = get_logger(__name__)

# Sentences that are navigation or boilerplate rather than assertions.
_BOILERPLATE_START = re.compile(
    r"^\s*(click|subscribe|sign up|read more|share this|©|copyright|follow us|"
    r"all rights reserved|cookie|privacy policy|terms of service|back to top|"
    r"related:|also read|advertisement|sponsored)",
    re.IGNORECASE,
)

# Verbs that make a sentence an assertion rather than a description.
#
# Stored as *stems* and matched by prefix: English inflects freely ("reduce",
# "reduced", "reducing") and listing every form is a losing game. Stems of two
# characters or fewer are compared whole, so "is" does not match "issue".
_ASSERTIVE_STEMS = (
    "is",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "will",
    "would",
    "can",
    "could",
    "may",
    "might",
    "must",
    "plan",
    "expect",
    "report",
    "measur",
    "show",
    "reduc",
    "increas",
    "cost",
    "score",
    "train",
    "releas",
    "deliver",
    "retain",
    "fall",
    "fell",
    "risen",
    "rise",
    "begin",
    "began",
    "start",
    "launch",
    "ship",
    "announc",
    "publish",
    "revis",
    "updat",
    "becom",
    "remain",
    "achiev",
    "reach",
    "contain",
    "remain",
    "stand",
    "rang",
    "come",
)
_ASSERTIVE_ZH = (
    "是",
    "为",
    "将",
    "已",
    "达到",
    "达到",
    "降低",
    "开始",
    "增加",
    "计划",
    "预计",
    "发布",
    "推出",
    "量产",
    "上市",
    "推迟",
    "保留",
    "得分",
    "训练",
    "包含",
    "来自",
)

_PREDICTION_STEMS = ("will", "expect", "plan", "likely", "forecast", "aim", "intend")
_PREDICTION_ZH = ("将", "计划", "预计", "拟", "有望")

_OPINION_STEMS = ("believe", "think", "should", "arguabl", "seem", "reckon", "suggest")
_OPINION_ZH = ("认为", "应该", "或许")

_EVENT_STEMS = ("production", "launch", "release", "ship", "begin", "start", "postpon", "delay")
_EVENT_ZH = ("量产", "发布", "上市", "推迟", "上线", "开始")

_CJK = re.compile(r"[㐀-鿿]")

# "HX-2", "Orchid-7": alphanumerics containing a digit are entity-shaped.
_ENTITY_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*\b")
_CAPITALIZED = re.compile(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)+\b")

MIN_TOKENS_EN = 3
MIN_CHARS_ZH = 8
MAX_TOKENS_EN = 60
MAX_CHARS_ZH = 120

MAX_CLAIMS = 12


@dataclass
class ClaimDraft:
    """A candidate claim before it is aligned and persisted."""

    statement: str
    quote: str
    char_start: int
    char_end: int
    claim_type: str = "fact"
    subject: str = ""
    predicate: str = ""
    object: str = ""
    numbers: list[dict] = field(default_factory=list)
    qualifiers: dict = field(default_factory=dict)
    polarity: int = 1
    confidence: float = 0.5


def _is_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def _content_len(text: str) -> int:
    return len(content_tokens(text)) if not _is_cjk(text) else len(_CJK.findall(text))


_MODAL_STEMS = {"can", "could", "will", "would", "may", "might", "must", "shall", "should"}
_AUX_STEMS = {"is", "are", "was", "were", "has", "have", "had", "do", "does", "did"}


def _matches_stem(token: str, stems: tuple[str, ...]) -> bool:
    """Prefix match for inflected verbs, whole match for short ones and modal auxiliaries.

    "reduce", "reduced" and "reducing" all start with "reduc"; "is" must not match
    "issue", and modal auxiliaries like "can" or "will" must not match "cancer" or "William".
    """
    token_lower = token.casefold()
    for stem in stems:
        stem_lower = stem.casefold()
        if len(stem_lower) <= 2 or stem_lower in _MODAL_STEMS or stem_lower in _AUX_STEMS:
            if token_lower == stem_lower:
                return True
        elif token_lower.startswith(stem_lower):
            return True
    return False


def _has_stem(text: str, stems: tuple[str, ...], zh: tuple[str, ...] = ()) -> bool:
    """Does the sentence use any of these stems?

    Over *raw* words, not `content_tokens`: that helper strips stopwords for
    similarity scoring, but "will", "is" and "are" are stopwords and are exactly the
    verbs that decide whether a sentence is a prediction or a bare assertion.
    """
    if any(word in text for word in zh):
        return True
    return any(_matches_stem(word, stems) for word in _words(text))


def _has_assertive_verb(text: str) -> bool:
    return _has_stem(text, _ASSERTIVE_STEMS, _ASSERTIVE_ZH)


def _classify(text: str) -> str | None:
    """One of fact / number / date / prediction / opinion.

    Precedence follows the plan: a quantity makes it a `number`, a date attached to
    an event makes it a `date`, and only then does predictive phrasing count. The
    order matters for sentences carrying several signals -- "production will begin
    in September 2026" is about *when*, and its informative content is the date, not
    the future tense.
    """
    if extract_quantities(text):
        return "number"
    if _has_date(text) and _has_stem(text, _EVENT_STEMS, _EVENT_ZH):
        return "date"
    if _has_stem(text, _PREDICTION_STEMS, _PREDICTION_ZH):
        return "prediction"
    if _has_stem(text, _OPINION_STEMS, _OPINION_ZH):
        return "opinion"
    return "fact"


def _has_date(text: str) -> bool:
    from app.claims.numbers import extract_dates

    return bool(extract_dates(text))


def _subject_of(text: str) -> str:
    """The sentence's leading entity, for grouping and for the wiki's headings."""
    for entity in _ENTITY_TOKEN.finditer(text):
        val = entity.group(0)
        if not any(c.isdigit() for c in val):
            continue
        if re.match(r"^(?:Q[1-4]|FY\d{2,4}|H[1-2]|\d{4})$", val, re.IGNORECASE):
            continue
        return val
    for capitalized in _CAPITALIZED.finditer(text):
        val = capitalized.group(0)
        if not re.match(r"^(?:In|On|At|By|During|Since|Before|After)\s+[A-Z][a-z]+$", val):
            return val
    tokens = content_tokens(text)
    return " ".join(tokens[:3])


def _numbers_of(text: str) -> list[dict]:
    return [{"value": q.value, "unit": q.unit, "text": q.raw} for q in extract_quantities(text)]


def _polarity_of(text: str) -> int:
    from app.claims.signals import _has_any

    return -1 if _has_any(text, NEGATIONS) else 1


def is_claim_sentence(sentence: str) -> bool:
    """Would this sentence make a citable claim?"""
    stripped = sentence.strip()
    if not stripped or stripped.endswith("?"):
        return False
    if _BOILERPLATE_START.match(stripped):
        return False

    if _is_cjk(stripped):
        length = len(_CJK.findall(stripped))
        if length < MIN_CHARS_ZH or length > MAX_CHARS_ZH:
            return False
    else:
        length = _content_len(stripped)
        if length < MIN_TOKENS_EN or length > MAX_TOKENS_EN:
            return False

    # A quantity, a date, or an explicit assertion. Without one of these the
    # sentence is description, not something a source could contradict.
    if extract_quantities(stripped):
        return True
    if _has_date(stripped):
        return True
    return _has_assertive_verb(stripped)


def heuristic_claims(chunk_text: str, chunk_start: int, *, stats: Any = None) -> list[ClaimDraft]:
    """Every claim-bearing sentence in a chunk, as drafts with absolute offsets."""
    candidates: list[ClaimDraft] = []
    for span in split_sentences(chunk_text):
        sentence = span.text_of(chunk_text)
        if not is_claim_sentence(sentence):
            continue
        candidates.append(
            ClaimDraft(
                statement=sentence.strip(),
                # The quote is the source text itself; alignment re-reads it anyway.
                quote=sentence,
                char_start=chunk_start + span.start,
                char_end=chunk_start + span.end,
                claim_type=_classify(sentence) or "fact",
                subject=_subject_of(sentence),
                numbers=_numbers_of(sentence),
                polarity=_polarity_of(sentence),
                confidence=0.6 if extract_quantities(sentence) else 0.5,
            )
        )
    if len(candidates) > MAX_CLAIMS:
        truncated = len(candidates) - MAX_CLAIMS
        log.info(
            "heuristic_claims_truncated: total=%d, limit=%d, truncated=%d",
            len(candidates),
            MAX_CLAIMS,
            truncated,
        )
        if stats is not None and hasattr(stats, "rejected_over_limit"):
            stats.rejected_over_limit += truncated
        return candidates[:MAX_CLAIMS]
    return candidates


__all__ = [
    "MAX_CLAIMS",
    "ClaimDraft",
    "heuristic_claims",
    "is_claim_sentence",
]
