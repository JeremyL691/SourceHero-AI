"""Quote alignment: map a model-supplied quote back to exact source offsets.

This is the load-bearing integrity check of the whole product. A language model asked
to quote will occasionally paraphrase, silently normalise a character, or invent a
span. Storing whatever it returned would put text in the ledger that no source ever
said -- and every downstream claim, citation and conflict would inherit the lie.

So alignment tries three strategies in order of strictness and **rejects** anything
that cannot be located in the source. A claim with no locatable quote is dropped; it
is better to have no claim than an unfalsifiable one.
"""

from __future__ import annotations

import unicodedata

from rapidfuzz import fuzz

# Score at or above which a fuzzy match is accepted.
FUZZY_CUTOFF = 90

_QUOTES = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "–": "-",
    "—": "-",
    "‒": "-",
    "―": "-",
    "‐": "-",
    "‑": "-",
    "−": "-",
    " ": " ",
    " ": " ",
    " ": " ",
    "　": " ",
}
_TABLE = str.maketrans(_QUOTES)


def _normalize(text: str) -> str:
    """Fold the differences a model may introduce without changing the wording.

    Whitespace runs collapse to one space rather than vanishing: word boundaries are
    real signal here, and dropping them here while `_index_map` kept them made the
    exact and fuzzy paths disagree about what "normalized" means.
    """
    chars: list[str] = []
    previous_was_space = False
    for ch in text:
        folded = _TABLE.get(ch, ch)
        if unicodedata.combining(folded):
            continue
        for piece in unicodedata.normalize("NFKC", folded):
            if unicodedata.combining(piece):
                continue
            if piece.isspace():
                if previous_was_space:
                    continue
                previous_was_space = True
                piece = " "
            else:
                previous_was_space = False
            for c in piece.casefold():
                chars.append(c)
    return "".join(chars).strip()


def _index_map(text: str) -> tuple[str, list[int]]:
    """The normalized form plus, for each of its characters, the original index."""
    chars: list[str] = []
    positions: list[int] = []
    previous_was_space = False
    for index, ch in enumerate(text):
        folded = _TABLE.get(ch, ch)
        if unicodedata.combining(folded):
            continue
        for piece in unicodedata.normalize("NFKC", folded):
            if unicodedata.combining(piece):
                continue
            if piece.isspace():
                if previous_was_space:
                    continue
                previous_was_space = True
                piece = " "
            else:
                previous_was_space = False
            for c in piece.casefold():
                chars.append(c)
                positions.append(index)
    return "".join(chars), positions


def _expand_combining(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and unicodedata.combining(text[start]):
        start -= 1
    while end < len(text) and unicodedata.combining(text[end]):
        end += 1
    return start, end


def align_quote(quote: str, chunk_text: str, chunk_start: int = 0) -> tuple[int, int] | None:
    """Locate `quote` in `chunk_text`; return absolute offsets or None.

    `chunk_start` is added to both ends, so the result addresses the version's full
    text rather than the chunk's slice of it.
    """
    if not quote or not chunk_text:
        return None

    # 1. Exact. The common case when the model copied faithfully.
    index = chunk_text.find(quote)
    if index >= 0:
        return chunk_start + index, chunk_start + index + len(quote)

    normalized_chunk, positions = _index_map(chunk_text)
    normalized_quote = _normalize(quote)
    if not normalized_quote:
        return None

    # 2. Normalized: same words, different quotes, spacing or dashes.
    index = normalized_chunk.find(normalized_quote)
    if index >= 0:
        start = positions[index]
        last = index + len(normalized_quote) - 1
        end = positions[last] + 1
        start, end = _expand_combining(chunk_text, start, end)
        return chunk_start + start, chunk_start + end

    # 3. Fuzzy: one typo, or a truncation at a token boundary.
    #    `partial_ratio_alignment` returns offsets into the *needle*, which is what
    #    maps back through `positions` into the chunk.
    result = fuzz.partial_ratio_alignment(normalized_quote, normalized_chunk, score_cutoff=FUZZY_CUTOFF)
    if result is None:
        return None
    # `dest_end` is exclusive, matching Python slice semantics.
    start = positions[result.dest_start]
    last = result.dest_end - 1
    if last >= len(positions):
        return None
    end = positions[last] + 1
    start, end = _expand_combining(chunk_text, start, end)
    return chunk_start + start, chunk_start + end


def contains(chunk_text: str, start: int, end: int) -> bool:
    """Does `[start, end)` address real characters of `chunk_text`?"""
    return 0 <= start < end <= len(chunk_text)


__all__ = ["FUZZY_CUTOFF", "align_quote", "contains"]
