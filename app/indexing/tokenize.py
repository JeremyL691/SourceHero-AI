"""Tokenization shared by indexing, retrieval and verification.

One tokenizer, so `search_text` written at index time and `build_tsquery(query)` at
query time always agree. jieba segments CJK; Latin runs through a regex that keeps
intra-token apostrophes and dots so "Orchid-7" and "u.s." survive intact.
"""

from __future__ import annotations

import logging
import re

import jieba

from app.indexing.stopwords import STOPWORDS

jieba.setLogLevel(logging.WARNING)

CJK_SPLIT = re.compile(r"([㐀-鿿]+)")
LATIN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")


def search_tokens(text: str) -> list[str]:
    """Lowercased, stopword-free tokens suitable for both tsvector and tsquery."""
    out: list[str] = []
    for seg in CJK_SPLIT.split(text.lower()):
        if not seg:
            continue
        if CJK_SPLIT.fullmatch(seg):
            out += [t for t in jieba.lcut_for_search(seg) if t.strip()]
        else:
            out += LATIN.findall(seg)
    return [t for t in out if t not in STOPWORDS]


def search_text(text: str) -> str:
    """The stored form: space-joined tokens. Postgres builds `tsv` from this."""
    return " ".join(search_tokens(text))


def build_tsquery(query: str) -> str | None:
    """OR-join the query's tokens, or None when nothing survives tokenization.

    Tokens are single-quoted so Postgres treats them as lexemes rather than trying
    to parse `or`, `and`, `-` and friends as operators.
    """
    tokens = sorted(set(search_tokens(query)))
    if not tokens:
        return None
    return " | ".join("'" + t.replace("'", "''") + "'" for t in tokens)


__all__ = ["build_tsquery", "search_text", "search_tokens"]
