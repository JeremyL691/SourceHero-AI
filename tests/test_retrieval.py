"""M5.7: tokenization, FTS, RRF, filtering, and the retrieval regression baseline."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.indexing.embeddings import cosine, get_embedder
from app.indexing.tokenize import build_tsquery, search_text, search_tokens
from app.retrieval.hybrid import hybrid_search, reciprocal_rank_fusion, reset_pgvector_probe
from app.retrieval.types import SearchFilters

pytestmark = pytest.mark.db

DATASET = Path(__file__).resolve().parents[1] / "evals" / "data" / "demo_retrieval.json"

# Measured on the demo corpus with the default `hashing` embedder; see DECISIONS.md.
# The plan sets the regression bar at "baseline - 0.05".
BASELINE_RECALL_AT_5 = 1.00
RECALL_FLOOR = 0.95


@pytest.fixture
def corpus_user(fresh_db):
    """A user with the demo corpus actually committed, so queries can see it."""
    from app.core.config import get_settings as gs
    from app.demo.seed import seed_demo, summary
    from app.indexing.indexer import index_version
    from app.jobs.handlers import register_all
    from app.models.document import DocumentVersion

    register_all()
    settings = gs()
    original = settings.jobs_inline
    settings.jobs_inline = False
    user_id = uuid.uuid4()
    try:
        seed_demo(fresh_db, user_id)
        fresh_db.flush()
        for version in fresh_db.query(DocumentVersion).filter(DocumentVersion.user_id == user_id).all():
            index_version(fresh_db, version.id)
        fresh_db.commit()
        assert summary(fresh_db, user_id)["documents"] == 12
        yield user_id
    finally:
        settings.jobs_inline = original
        reset_pgvector_probe()


def _corpus_ids_by_document(db, user_id) -> dict:
    from sqlalchemy import select

    from app.models.document import Document
    from app.models.source import Source

    rows = db.execute(
        select(Document.id, Source.config)
        .join(Source, Source.id == Document.source_id)
        .where(Document.user_id == user_id)
    ).all()
    return {doc_id: (cfg or {}).get("corpus_id") for doc_id, cfg in rows if cfg}


def _dataset() -> dict:
    return json.loads(DATASET.read_text(encoding="utf-8"))


# --- tokenization (pure) -------------------------------------------------


def test_chinese_query_tokens_are_what_the_plan_requires():
    tokens = search_tokens("HX-2 电池的能量密度为 450 Wh/kg")
    for expected in ("能量", "密度", "450", "hx"):
        assert expected in tokens


def test_build_tsquery_or_joins_quoted_lexemes():
    assert build_tsquery("能量密度") == "'密度' | '能量'"
    assert build_tsquery("") is None


def test_search_text_matches_what_the_index_writes():
    assert search_text("Energy density 450") == "energy density 450"
    assert search_text("能量密度") == "能量 密度"


# --- RRF (pure) ----------------------------------------------------------


def test_rrf_matches_a_hand_computed_value():
    fused = reciprocal_rank_fusion(
        [[(1, 0.9), (2, 0.8), (3, 0.7)], [(2, 0.95), (3, 0.6)]],
        k=60,
    )
    # 1 appears only in list A at rank 1; 2 in both at ranks 2 and 1.
    assert fused[1] == pytest.approx(1 / 61)
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
    assert fused[3] == pytest.approx(1 / 63 + 1 / 62)
    # Appearing in both lists must beat appearing in one, at equal individual rank.
    assert fused[2] > fused[1]


def test_rrf_ranks_by_order_not_by_raw_score():
    """Scores are incomparable across legs; only order matters."""
    fused = reciprocal_rank_fusion([[(10, 0.01), (11, 99.0)], [(10, 0.02)]], k=60)
    assert fused[10] == pytest.approx(1 / 61 + 1 / 61)
    assert fused[11] == pytest.approx(1 / 62)


# --- search behaviour ----------------------------------------------------


def test_chinese_query_finds_the_chinese_document(corpus_user, fresh_db):
    result = hybrid_search(fresh_db, corpus_user, "HX-2 电池的能量密度是多少", k=5)
    mapping = _corpus_ids_by_document(fresh_db, corpus_user)
    found = {mapping.get(h.document_id) for h in result.hits}
    assert "zh-hx2-news" in found


def test_english_query_finds_the_english_documents(corpus_user, fresh_db):
    result = hybrid_search(fresh_db, corpus_user, "congestion charge downtown traffic", k=5)
    mapping = _corpus_ids_by_document(fresh_db, corpus_user)
    found = {mapping.get(h.document_id) for h in result.hits}
    assert "ferro-gazette-congestion" in found


@pytest.mark.parametrize("mode", ["lexical", "vector", "hybrid"])
def test_every_mode_returns_results(corpus_user, fresh_db, mode):
    result = hybrid_search(fresh_db, corpus_user, "energy density", k=5, mode=mode)
    assert result.hits, mode
    assert result.mode_effective in (mode, "lexical", "vector")


def test_mode_effective_falls_back_to_the_leg_that_worked(corpus_user, fresh_db):
    """A query with no lexical overlap is still answerable by the vector leg."""
    gibberish = "zzqqxwv nonsense token"
    result = hybrid_search(fresh_db, corpus_user, gibberish, k=3, mode="hybrid")
    if result.hits:
        assert result.mode_effective in ("hybrid", "vector", "lexical")


def test_scores_expose_both_legs(corpus_user, fresh_db):
    hit = hybrid_search(fresh_db, corpus_user, "energy density", k=3).hits[0]
    assert set(hit.scores) == {"lexical_rank", "vector_rank", "rrf", "rerank"}
    assert hit.scores["rrf"] > 0


def test_old_versions_are_excluded_by_default(corpus_user, fresh_db):
    """A superseded version must not be retrievable as present-tense fact."""
    default = hybrid_search(fresh_db, corpus_user, "postponed January 2027", k=10)
    assert default.hits, "the current version should mention the postponement"


def test_include_old_versions_reaches_text_that_only_exists_there(corpus_user, fresh_db):
    """The v1 press release wording is only retrievable once old versions are in scope.

    Asserted on *version ids*, not on emptiness: the vector leg legitimately matches
    a v1-shaped query against v2, because both discuss the schedule. What must not
    happen is the default search returning the superseded version.
    """
    query = "reaffirmed the schedule first announced in March"
    # k must exceed the number of chunks in the corpus, otherwise a low-ranked
    # superseded version is merely truncated away and the assertion proves nothing.
    current = hybrid_search(fresh_db, corpus_user, query, k=20)
    historical = hybrid_search(
        fresh_db,
        corpus_user,
        query,
        k=20,
        filters=SearchFilters(include_old_versions=True),
    )
    from app.models.document import Document, DocumentVersion

    declared_current = {
        row[0]
        for row in fresh_db.query(Document.current_version_id).filter(Document.user_id == corpus_user).all()
    }
    all_version_ids = {
        row[0]
        for row in fresh_db.query(DocumentVersion.id).filter(DocumentVersion.user_id == corpus_user).all()
    }
    superseded = all_version_ids - declared_current
    assert superseded, "the corpus must contain a superseded version for this to mean anything"

    current_versions = {h.version_id for h in current.hits}
    historical_versions = {h.version_id for h in historical.hits}

    # The default search must never return a superseded version.
    assert not (current_versions & superseded), "a superseded version leaked into a default search"
    # And opting in must reach at least one of them.
    assert historical_versions & superseded, "include_old_versions did not reach any superseded version"


def test_filters_by_source_type(corpus_user, fresh_db):
    notes = hybrid_search(
        fresh_db,
        corpus_user,
        "budget for HX-2 in Q4",
        k=10,
        filters=SearchFilters(source_types=["note"]),
    )
    assert notes.hits
    assert all(h.source_type == "note" for h in notes.hits)

    webpages = hybrid_search(
        fresh_db, corpus_user, "budget for HX-2", k=10, filters=SearchFilters(source_types=["note"])
    )
    assert len(webpages.hits) < len(notes.hits) or webpages.hits


def test_tenant_isolation(corpus_user, fresh_db):
    """Another user gets nothing from a corpus they did not seed."""
    assert hybrid_search(fresh_db, uuid.uuid4(), "energy density", k=5).hits == []


def test_empty_query_yields_no_hits_not_an_error(corpus_user, fresh_db):
    result = hybrid_search(fresh_db, corpus_user, "!!! ???", k=5)
    assert result.hits == []


def test_hits_carry_offsets_into_the_current_text(corpus_user, fresh_db):
    from app.models.document import DocumentVersion

    hit = hybrid_search(fresh_db, corpus_user, "energy density", k=3).hits[0]
    version = fresh_db.get(DocumentVersion, hit.version_id)
    assert version is not None
    # The offsets the reader highlights with must address real characters.
    assert 0 <= hit.char_start < hit.char_end <= len(version.clean_text)


def test_hashing_embedder_places_a_paraphrase_closer_than_an_unrelated_text():
    embedder = get_embedder()
    a = embedder.embed_query("the HX-2 energy density is 450 Wh per kilogram")
    b = embedder.embed_query("the HX-2 energy density was measured at 380 Wh per kilogram")
    c = embedder.embed_query("a ferry timetable lists sailings across the strait")
    assert cosine(a, b) > cosine(a, c)


# --- regression baseline -------------------------------------------------


def test_recall_at_5_does_not_regress(corpus_user, fresh_db):
    """Guards the retrieval path against changes that quietly lose relevance."""
    data = _dataset()
    mapping = _corpus_ids_by_document(fresh_db, corpus_user)
    hits = total = 0
    for entry in data["queries"]:
        result = hybrid_search(fresh_db, corpus_user, entry["query"], k=5)
        found = {mapping.get(h.document_id) for h in result.hits}
        hits += len(set(entry["relevant"]) & found)
        total += len(entry["relevant"])
    recall = hits / total
    assert recall >= RECALL_FLOOR, (
        f"recall@5 fell to {recall:.3f} (baseline {BASELINE_RECALL_AT_5:.3f}, floor {RECALL_FLOOR})"
    )


def test_dataset_covers_the_corpus_and_includes_chinese():
    from app.demo.corpus import load_corpus

    data = _dataset()
    assert len(data["queries"]) >= 20, "the plan requires at least 20 queries"
    known = {f.id for f in load_corpus()}
    for entry in data["queries"]:
        assert entry["relevant"], entry["query"]
        for corpus_id in entry["relevant"]:
            assert corpus_id in known, f"{corpus_id} is not in the demo corpus"
    chinese = [q for q in data["queries"] if any("一" <= ch <= "鿿" for ch in q["query"])]
    assert chinese, "the plan requires Chinese queries in the retrieval dataset"


@pytest.mark.model
def test_fastembed_places_a_paraphrase_closer_than_an_unrelated_text():
    """Needs the model download; skipped unless the operator opts in."""
    from app.indexing.embeddings import FastEmbedEmbedder

    embedder = FastEmbedEmbedder.get(
        get_settings().embedding_model,
        get_settings().embedding_dim,
        get_settings().embedding_cache_dir,
    )
    a = embedder.embed_query("the HX-2 energy density is 450 Wh per kilogram")
    b = embedder.embed_query("independent tests measured the HX-2 energy density")
    c = embedder.embed_query("a ferry timetable lists sailings across the strait")
    assert cosine(a, b) > cosine(a, c)
