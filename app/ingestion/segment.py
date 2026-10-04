"""Sentence segmentation with character offsets.

The contract every caller relies on: for a returned span, `text[s.start:s.end]`
stripped of surrounding whitespace *is* the sentence. Claim extraction, quote
alignment, evidence highlights and the verifier all use these offsets directly.

Handles Chinese and Western text, abbreviations, decimals, single initials and
closing quotes. A paragraph break (`\\n\\n`) is always a sentence boundary, which is
what keeps prose blocks intact when a paragraph contains no terminal punctuation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Full-width terminators, and '…' plus ASCII equivalents.
_CJK_TERMINATORS = "。！？；…"
_ASCII_TERMINATORS = ".!?"

# Abbreviations that end in '.' but do not end a sentence.
_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "st",
    "rev",
    "hon",
    "gen",
    "col",
    "lt",
    "capt",
    "sgt",
    "inc",
    "ltd",
    "co",
    "corp",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "approx",
    "est",
    "fig",
    "no",
    "al",
    "vol",
    "pp",
    "dept",
    "univ",
    "assn",
    "bros",
    "mfg",
    "govt",
    "sen",
    "rep",
    "ltc",
}

_INITIAL = re.compile(r"^[A-Z]$")
_TRAILING_CLOSERS = "\"')]}»›”’"
_WORD_BEFORE_DOT = re.compile(r"([A-Za-z0-9.'\u00c0-\u024f]+)\.$")
_NUMBER_BEFORE_DOT = re.compile(r"\d\.$")


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    def text_of(self, source: str) -> str:
        return source[self.start : self.end]


def _is_terminator(ch: str) -> bool:
    return ch in _CJK_TERMINATORS or ch in _ASCII_TERMINATORS


def _closes_sentence(text: str, index: int) -> bool:
    """Does the terminator at `index` end a sentence?

    Handles: CJK terminators, decimals (`3.5`), abbreviations (`Dr. Smith`),
    single initials (`J. K. Rowling`), and trailing quotes/brackets.
    """
    ch = text[index]
    if ch in _CJK_TERMINATORS:
        return True
    if ch not in _ASCII_TERMINATORS:
        return False

    if ch == ".":
        # Look back over the token ending here.
        match = _WORD_BEFORE_DOT.search(text[: index + 1])
        if not match:
            # A bare '.' with no word before it (list markers) ends the sentence.
            return True
        token = match.group(1)
        # Decimal: `3.` -- a digit on both sides is a number, not an abbreviation.
        if _NUMBER_BEFORE_DOT.search(text[: index + 1]) and _next_is_digit(text, index):
            return False
        bare = token.strip(".'").lower()
        if bare in _ABBREVIATIONS:
            return False
        if _INITIAL.match(token.strip(".")) and not token.endswith(".."):
            # `J.` in "J. K. Rowling" is an initial, not an abbreviation end.
            # A dotted acronym like "U.S." is handled by the abbreviation set.
            return False
    # Look ahead: a terminator only ends a sentence if whitespace (or end) follows,
    # optionally after a closing quote or bracket.
    j = index + 1
    while j < len(text) and text[j] in _TRAILING_CLOSERS:
        j += 1
    if j >= len(text):
        return True
    if not text[j].isspace():
        return False
    # The next non-space character starts the next sentence if it is a capital
    # letter, a CJK character, an opening quote, or a digit.
    k = j
    while k < len(text) and text[k].isspace():
        k += 1
    if k >= len(text):
        return True
    nxt = text[k]
    if nxt.isupper() or nxt.isdigit():
        return True
    if "\u4e00" <= nxt <= "\u9fff":
        return True
    return nxt in "\"'([{«“‘"


def _next_is_digit(text: str, index: int) -> bool:
    j = index + 1
    return j < len(text) and text[j].isdigit()


def split_sentences(text: str) -> list[Span]:
    """Split `text` into sentence spans, trimming surrounding whitespace.

    Empty and whitespace-only input yields an empty list.
    """
    if not text or not text.strip():
        return []

    spans: list[Span] = []
    start = 0
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        # Paragraph break is always a boundary.
        if ch == "\n":
            j = i
            while j < n and text[j] == "\n":
                j += 1
            if j - i >= 2:
                spans.append(Span(start, i))
                start = j
                i = j
                continue
            i += 1
            continue

        if _is_terminator(ch) and _closes_sentence(text, i):
            # Absorb any closing quotes/brackets that belong to this sentence.
            end = i + 1
            while end < n and text[end] in _TRAILING_CLOSERS:
                end += 1
            spans.append(Span(start, end))
            start = end
            i = end
            continue

        i += 1

    spans.append(Span(start, n))

    # Trim and drop empties. Trimming is what makes `text[start:end].strip()` equal
    # the sentence for callers that re-slice without the whitespace.
    out: list[Span] = []
    for span in spans:
        s, e = span.start, span.end
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if s < e:
            out.append(Span(s, e))
    return out


__all__ = ["Span", "split_sentences"]
