"""Tokenization and the hashing embedder.

Both are pure functions, so this file needs no database.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.indexing.embeddings import HashingEmbedder, cosine, get_embedder
from app.indexing.tokenize import build_tsquery, search_text, search_tokens

# --- tokenization --------------------------------------------------------


def test_chinese_and_latin_tokens_match_the_plan():
    tokens = search_tokens("HX-2 电池的能量密度为 450 Wh/kg")
    for expected in ("能量", "密度", "450", "hx"):
        assert expected in tokens, f"{expected!r} missing from {tokens}"


def test_tokens_are_lowercased_and_stopword_free():
    tokens = search_tokens("The report was published in the Wall Street Journal")
    assert "the" not in tokens
    assert "report" in tokens
    assert all(t == t.lower() for t in tokens)


def test_tokenizer_is_deterministic():
    text = "Orchid-7 was trained on 2 trillion tokens"
    assert search_tokens(text) == search_tokens(text)


def test_empty_and_punctuation_only_input():
    assert search_tokens("") == []
    assert search_tokens("   ...   ") == []


def test_build_tsquery_quotes_and_joins_with_or():
    q = build_tsquery("能量密度")
    assert q == "'密度' | '能量'"
    assert build_tsquery("the and of") is None


def test_build_tsquery_escapes_embedded_quotes():
    # A raw apostrophe would otherwise close the lexeme and inject tsquery syntax.
    q = build_tsquery("the company's costs")
    assert q is not None
    assert "''" in q
    assert q.count("'") % 2 == 0


def test_search_text_is_space_joined_tokens():
    assert search_text("能量密度") == "能量 密度"


# --- hashing embedder ----------------------------------------------------


@pytest.fixture(scope="module")
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


def test_embeddings_are_unit_length(embedder):
    for text in ("hello world", "HX-2 电池", "congestion charge"):
        vec = embedder.embed_query(text)
        assert pytest.approx(1.0, abs=1e-9) == float(np.linalg.norm(vec)), text


def test_tokenless_text_yields_a_zero_vector_not_nans(embedder):
    # Nothing survives tokenization, so there is nothing to embed. The contract is
    # "zero vector", not a vector full of NaNs that would poison pgvector.
    for text in ("", "   ", "the and of"):
        vec = np.asarray(embedder.embed_query(text))
        assert not np.isnan(vec).any(), text
        assert np.linalg.norm(vec) == pytest.approx(0.0), text
    assert cosine(embedder.embed_query(""), embedder.embed_query("anything")) == 0.0


def test_embeddings_are_deterministic(embedder):
    a = embedder.embed_query("Mass production will begin in September 2026")
    b = embedder.embed_query("Mass production will begin in September 2026")
    assert a == b


def test_dim_matches_settings(embedder):
    from app.core.config import get_settings

    assert embedder.dim == get_settings().embedding_dim


def test_related_texts_score_above_unrelated(embedder):
    a = embedder.embed_query("The HX-2 cell delivers an energy density of 450 Wh/kg.")
    b = embedder.embed_query("Independent lab tests measured the HX-2 energy density at 380 Wh/kg.")
    c = embedder.embed_query("The mayor opened a new bridge over the river.")
    assert cosine(a, b) > cosine(a, c)


def test_get_embedder_is_the_hashing_one_by_default():
    assert get_embedder().name == "hashing-v1"
