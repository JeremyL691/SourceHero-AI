"""Sentence-packed chunking.

Chunks are built from sentence spans, never from raw character windows, so a sentence
is never split across two chunks. Every chunk carries absolute character offsets into
`clean_text`, and `text[char_start:char_end]` is exactly the chunk's text.

Invariants (asserted in tests, and relied on by claims, citations and highlights):
  * every chunk is non-empty after stripping;
  * the union of all chunks covers every non-whitespace character of the text;
  * chunk starts increase monotonically;
  * `version.clean_text[chunk.char_start:chunk.char_end] == chunk.text`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.segment import Span, split_sentences

DEFAULT_TARGET_TOKENS = 220
DEFAULT_MAX_TOKENS = 320
DEFAULT_OVERLAP_SENTENCES = 1

_CJK = re.compile(r"[㐀-鿿豈-﫿぀-ヿ]")
_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class ChunkSpan:
    """A chunk's absolute offsets into `clean_text`, plus its token estimate.

    `text[start:end] == chunk.text` always holds; the estimate is filled in by
    `chunk_spans` (which has the text) and defaults to 0 when built by hand.
    """

    start: int
    end: int
    index: int
    tokens: int = 0


def estimate_tokens(text: str) -> int:
    """Rough token count: one per CJK character, one per 4 Latin characters.

    Deliberately an estimate -- it only decides when to start a new chunk.
    """
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk) // 4


def _char_budget(inner: str, max_tokens: int) -> int:
    """How many characters of `inner` are worth `max_tokens` tokens.

    Derived from this text's own density rather than a fixed 4: pure Latin runs about
    4 characters per token, pure CJK about 1. A fixed ratio would let a long Chinese
    sentence overflow fourfold.
    """
    cjk = len(_CJK.findall(inner))
    latin = len(inner) - cjk
    tokens = cjk + latin // 4
    if tokens <= 0:
        return max(1, max_tokens * 4)
    return max(1, int(max_tokens * len(inner) / tokens))


def _hard_split(text: str, start: int, end: int, max_tokens: int) -> list[Span]:
    """Split an oversized single sentence at word boundaries.

    When a single word is longer than the budget there is no boundary to back off
    to, so it is cut mid-word rather than allowed to blow the limit -- an
    unrecoverable chunk would poison the embedding and every downstream offset.
    """
    inner = text[start:end]
    out: list[Span] = []
    cursor = 0
    length = len(inner)
    budget = _char_budget(inner, max_tokens)

    while cursor < length:
        window = min(length, cursor + budget)
        if window < length:
            cut = inner.rfind(" ", cursor + 1, window)
            if cut > cursor:
                window = cut
        piece = inner[cursor:window]
        if piece.strip():
            out.append(Span(start + cursor, start + window))
        if window == cursor:  # no progress is impossible; guard anyway
            window = cursor + 1
        cursor = window
    return out


def chunk_spans(
    text: str,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
) -> list[ChunkSpan]:
    """Pack sentences into chunks, overlapping by `overlap_sentences`.

    A sentence longer than `max_tokens` is hard-split at word boundaries rather than
    being allowed to blow the limit.
    """
    if not text or not text.strip():
        return []

    sentences = split_sentences(text)
    if not sentences:
        return []

    # Explode any oversized sentence first, so packing only ever adds.
    units: list[Span] = []
    for span in sentences:
        if estimate_tokens(text[span.start : span.end]) > max_tokens:
            units.extend(_hard_split(text, span.start, span.end, max_tokens))
        else:
            units.append(span)
    if not units:
        return []

    # Greedy packing with a trailing overlap window.
    groups: list[list[Span]] = []
    current: list[Span] = []
    current_tokens = 0

    def seed_overlap() -> tuple[list[Span], int]:
        if overlap_sentences <= 0 or not current:
            return [], 0
        carried = current[-overlap_sentences:]
        return list(carried), sum(estimate_tokens(text[s.start : s.end]) for s in carried)

    for unit in units:
        unit_tokens = estimate_tokens(text[unit.start : unit.end])

        if current and current_tokens + unit_tokens > max_tokens:
            # Closing on the hard limit: drop the overlap rather than exceed it.
            groups.append(current)
            current, current_tokens = [], 0

        if current and current_tokens + unit_tokens > target_tokens:
            # Soft boundary: close and carry the overlap forward.
            groups.append(current)
            carried, carried_tokens = seed_overlap()
            while carried and carried_tokens + unit_tokens > max_tokens:
                # The overlap itself is what would overflow; give it up.
                carried = carried[1:]
                carried_tokens = sum(estimate_tokens(text[s.start : s.end]) for s in carried)
            current, current_tokens = carried, carried_tokens

        current.append(unit)
        current_tokens += unit_tokens
    if current:
        groups.append(current)

    out: list[ChunkSpan] = []
    for group in groups:
        start = group[0].start
        end = group[-1].end
        # Trim so `text[start:end]` has no surrounding whitespace.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end:
            continue
        out.append(ChunkSpan(start, end, len(out), estimate_tokens(text[start:end])))
    return out


__all__ = [
    "ChunkSpan",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_OVERLAP_SENTENCES",
    "DEFAULT_TARGET_TOKENS",
    "chunk_spans",
    "estimate_tokens",
]
