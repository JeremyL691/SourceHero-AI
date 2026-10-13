"""The shape every extractor returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExtractedDocument:
    """One document's content, ready to be versioned.

    `text` is raw; `upsert_document_version` normalizes it into `clean_text`, which
    is the only text any offset is ever measured against.
    """

    title: str = ""
    text: str = ""
    url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    lang: str | None = None
    page_map: list[dict] | None = None
    raw_text: str | None = None
    raw_bytes: bytes | None = None
    warnings: list[str] = field(default_factory=list)


def detect_language(text: str) -> str:
    """`zh` when CJK characters are over 30% of the sample, else `en`.

    A crude ratio is enough here: it only chooses which stopword list and which
    embedder path applies, and anything longer than a few thousand characters is
    unambiguous.
    """
    sample = text[:4000]
    if not sample:
        return "en"
    cjk = sum(1 for ch in sample if _is_cjk(ch))
    return "zh" if cjk / len(sample) > 0.30 else "en"


# CJK blocks used by `detect_language`: CJK Unified Ideographs (plus Ext A),
# Compatibility Ideographs, Hiragana/Katakana, and Hangul syllables.
_CJK_RANGES = (
    ("㐀", "䶿"),
    ("一", "鿿"),
    ("豈", "﫿"),
    ("぀", "ヿ"),
    ("가", "힯"),
)


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


__all__ = ["ExtractedDocument", "detect_language"]
