"""M4.9: extraction, versioning, chunking invariants, and the demo seed."""

from __future__ import annotations

import random
import uuid

import pytest

from app.core.text import normalize_text
from app.demo.corpus import load_corpus, to_extracted
from app.ingestion.canonical import canonicalize_url, identity_key
from app.ingestion.chunking import chunk_spans, estimate_tokens
from app.ingestion.fetch import FetchError, fetch, sanitize_error
from app.ingestion.pdf import extract_pdf, extract_text_file
from app.ingestion.segment import split_sentences
from app.ingestion.versions import upsert_document_version
from app.ingestion.webpage import extract_webpage
from app.jobs.registry import NonRetryableJobError
from app.models.document import Document, DocumentVersion
from app.models.source import Source

pytestmark = pytest.mark.db

USER = uuid.UUID("00000000-0000-0000-0000-0000000000d4")


# --- text and sentence segmentation --------------------------------------


def test_normalize_text_collapses_whitespace_but_keeps_paragraphs():
    # Removing NUL is a deletion, not a replacement: "e\x00f" becomes "ef".
    out = normalize_text("a  b\tc\r\n\r\nd\n\n\n\ne\x00f")
    assert out == "a b c\n\nd\n\nef"


def test_normalize_text_is_idempotent():
    once = normalize_text("Line one\nwrapped.\n\nSecond  para.")
    assert normalize_text(once) == once


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "The HX-2 cell delivers 450 Wh/kg. Mass production begins in September 2026.",
            2,
        ),
        ("HX-2 电池的能量密度为 450 Wh/kg。量产计划已经公布。", 2),
        ("Dr. Smith measured 3.5 hectares. J. K. Rowling wrote it. The U.S. market grew.", 3),
        ('He said "This is fine." Then he left.', 2),
        ("One paragraph.\n\nAnother paragraph.", 2),
        ("Just one sentence", 1),
        ("", 0),
        ("能量密度是多少？答案是 450。", 2),
    ],
)
def test_split_sentences(text, expected):
    assert len(split_sentences(text)) == expected


def test_sentence_spans_are_trimmed_and_cover_the_text():
    text = normalize_text("First sentence here.  Second one follows.\n\nA third sentence in a new paragraph.")
    spans = split_sentences(text)
    for span in spans:
        piece = text[span.start : span.end]
        assert piece == piece.strip()
        assert piece
    covered = "".join(text[s.start : s.end] for s in spans).replace(" ", "")
    assert covered == text.replace(" ", "").replace("\n", "")


# --- chunking ------------------------------------------------------------


def _random_text(rng: random.Random, sentences: int) -> str:
    en = ["energy", "density", "battery", "production", "market", "revenue", "cell"]
    zh = ["电池", "能量密度", "量产", "市场", "营收"]
    parts = []
    for _ in range(sentences):
        if rng.random() < 0.5:
            parts.append(
                " ".join(rng.choice(en) for _ in range(rng.randint(3, 12))) + rng.choice([".", "!", "?"])
            )
        else:
            parts.append("".join(rng.choice(zh) for _ in range(rng.randint(2, 6))) + "。")
    return normalize_text(" ".join(parts))


def test_chunking_invariants_hold_on_random_text():
    """Non-empty spans, monotonic starts, full coverage of every non-space character."""
    rng = random.Random(20260601)
    for _ in range(120):
        text = _random_text(rng, rng.randint(1, 30))
        chunks = chunk_spans(text)
        if not text.strip():
            assert chunks == []
            continue
        assert chunks, f"no chunks for non-empty text: {text[:60]!r}"

        starts = [c.start for c in chunks]
        assert starts == sorted(starts)
        assert [c.index for c in chunks] == list(range(len(chunks)))

        covered = set()
        for chunk in chunks:
            piece = text[chunk.start : chunk.end]
            assert piece.strip(), "empty chunk"
            assert piece == piece.strip()
            assert chunk.tokens <= 320, f"chunk over max_tokens: {chunk.tokens}"
            covered |= {i for i in range(chunk.start, chunk.end) if not text[i].isspace()}

        expected = {i for i, ch in enumerate(text) if not ch.isspace()}
        assert covered == expected, "chunks do not cover every non-space character"


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("latin", "word " * 400 + "."),
        ("cjk", "电池" * 900 + "。"),
        ("mixed", "HX-2 电池 " * 300 + "。"),
        ("unbreakable", "x" * 5000),
    ],
)
def test_oversized_input_never_exceeds_max_tokens(label, text):
    chunks = chunk_spans(normalize_text(text))
    assert chunks, label
    assert max(c.tokens for c in chunks) <= 320, label


def test_estimate_tokens_counts_cjk_per_character():
    assert estimate_tokens("电池量产") == 4
    assert estimate_tokens("abcd") == 1


def test_empty_input_yields_no_chunks():
    assert chunk_spans("") == []
    assert chunk_spans("   \n\n  ") == []


# --- canonical URLs ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM/Path/?utm_source=x&id=2&fbclid=9#frag", "https://example.com/Path?id=2"),
        ("http://example.com:80/a/b/", "http://example.com/a/b"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com/", "https://example.com"),
        ("https://example.com/?b=2&a=1", "https://example.com?a=1&b=2"),
        ("https://example.com/a/", "https://example.com/a"),
    ],
)
def test_canonicalize_url(raw, expected):
    assert canonicalize_url(raw) == expected


def test_identity_key_prefers_url_then_content():
    assert identity_key(url="https://Example.com/a/?utm_source=z") == "url:https://example.com/a"
    assert identity_key(clean_text="hi").startswith("hash:")
    assert identity_key(raw_bytes=b"hi").startswith("file:")
    with pytest.raises(ValueError):
        identity_key()


# --- fetch ---------------------------------------------------------------


def test_fetch_retries_then_raises_on_5xx(respx_mock):
    from app.core.config import get_settings

    get_settings().respect_robots = False
    route = respx_mock.get("https://example.test/a").mock(return_value=httpx_response(500, b"boom"))
    with pytest.raises(FetchError) as excinfo:
        fetch("https://example.test/a")
    assert excinfo.value.code == "http_5xx"
    assert route.call_count == 3, "5xx must be retried twice before giving up"


def test_fetch_does_not_retry_4xx(respx_mock):
    from app.core.config import get_settings

    get_settings().respect_robots = False
    route = respx_mock.get("https://example.test/missing").mock(return_value=httpx_response(404, b"nope"))
    with pytest.raises(FetchError) as excinfo:
        fetch("https://example.test/missing")
    assert excinfo.value.code == "http_4xx"
    assert route.call_count == 1, "4xx is final; asking again cannot help"


def test_fetch_rejects_oversized_bodies(respx_mock):
    from app.core.config import get_settings

    get_settings().respect_robots = False
    get_settings().http_max_bytes = 100
    respx_mock.get("https://example.test/big").mock(return_value=httpx_response(200, b"x" * 500))
    with pytest.raises(FetchError) as excinfo:
        fetch("https://example.test/big")
    assert excinfo.value.code == "too_large"


def test_robots_disallow_is_enforced(respx_mock):
    from app.core.config import get_settings

    settings = get_settings()
    settings.respect_robots = True
    respx_mock.get("https://blocked.test/robots.txt").mock(
        return_value=httpx_response(200, b"User-agent: *\nDisallow: /private\n"),
    )
    respx_mock.get("https://blocked.test/private/thing").mock(return_value=httpx_response(200, b"secret"))
    with pytest.raises(FetchError) as excinfo:
        fetch("https://blocked.test/private/thing")
    assert excinfo.value.code == "robots_disallowed"

    # An allowed path still works, and robots.txt is fetched only once per host.
    respx_mock.get("https://blocked.test/public").mock(return_value=httpx_response(200, b"ok"))
    assert fetch("https://blocked.test/public").content == b"ok"
    assert fetch("https://blocked.test/public").content == b"ok"
    robots_calls = [r for r in respx_mock.calls if "robots.txt" in str(r.request.url)]
    assert len(robots_calls) == 1, "robots.txt must be cached per host"


def test_unreachable_robots_treated_as_allowed(respx_mock):
    from app.core.config import get_settings

    get_settings().respect_robots = True
    respx_mock.get("https://brokentest.local/robots.txt").mock(return_value=httpx_response(500, b""))
    respx_mock.get("https://brokentest.local/page").mock(return_value=httpx_response(200, b"content"))
    assert fetch("https://brokentest.local/page").content == b"content"


def test_unsupported_scheme_is_rejected():
    with pytest.raises(FetchError) as excinfo:
        fetch("ftp://example.test/file")
    assert excinfo.value.code == "unsupported_scheme"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("timeout", "timed out"),
        ("dns", "could not be found"),
        ("robots_disallowed", "robots.txt"),
        ("too_large", "larger than"),
    ],
)
def test_sanitize_error_produces_one_readable_sentence(code, expected):
    message = sanitize_error(FetchError(code))
    assert expected in message
    assert "\n" not in message
    assert len(message) < 120


def test_sanitize_error_never_leaks_internals():
    message = sanitize_error(ValueError("/Users/someone/.ssh/id_rsa: Permission denied"))
    assert ".ssh" not in message and "id_rsa" not in message


def httpx_response(status: int, content: bytes):
    import httpx

    return httpx.Response(status, content=content)


# --- extractors ----------------------------------------------------------


def test_webpage_extraction_strips_chrome_but_keeps_claims(html_article):
    doc = extract_webpage(html_article, "https://demo.sourcehero.local/a")
    assert "The HX-2 cell delivers an energy density of 450 Wh/kg." in doc.text
    assert "Mass production of the HX-2 will begin in September 2026." in doc.text
    assert "Subscribe" not in doc.text
    assert "Copyright" not in doc.text
    assert doc.lang == "en"


def test_webpage_extraction_of_blog_finds_main_content(html_blog_with_nav):
    doc = extract_webpage(html_blog_with_nav, "https://demo.sourcehero.local/b")
    assert "Independent lab tests measured the HX-2 energy density at 380 Wh/kg." in doc.text
    assert "Related" not in doc.text


def test_nearly_empty_page_yields_no_text_and_a_warning(html_nearly_empty):
    doc = extract_webpage(html_nearly_empty, "https://demo.sourcehero.local/c")
    assert not doc.text.strip()
    assert doc.warnings, "an empty extraction must say so rather than look like a success"


def test_pdf_becomes_one_document_with_a_page_map(pdf_two_pages):
    doc = extract_pdf(pdf_two_pages, url="https://demo.test/x.pdf")
    assert "The HX-2 cell delivers an energy density of 450 Wh/kg." in doc.text
    assert "Mass production of the HX-2 will begin in September 2026." in doc.text
    assert doc.page_map and len(doc.page_map) == 2
    for entry in doc.page_map:
        # page_map offsets must index into this document's own text, so the reader
        # can highlight the original page.
        assert doc.text[entry["start"] : entry["end"]].strip()


def test_pdf_without_text_layer_warns(pdf_no_text_layer):
    doc = extract_pdf(pdf_no_text_layer)
    assert "no_text_layer" in doc.warnings


def test_malformed_pdf_raises_a_recorded_fetch_error():
    with pytest.raises(FetchError):
        extract_pdf(b"not a pdf at all")


def test_text_file_routes_by_extension():
    assert extract_text_file(b"# Heading\n\nBody.", filename="a.md").title == "Heading"
    assert extract_text_file("内容".encode("gb18030"), filename="b.txt").lang == "zh"
    html = "<html><body><article><h1>T</h1><p>Body sentence here.</p></article></body></html>"
    assert "Body sentence here." in extract_text_file(html.encode(), filename="c.html").text


def test_rss_extraction_uses_entry_fallbacks(respx_mock, rss_feed):
    from app.core.config import get_settings
    from app.ingestion.rss import extract_feed

    get_settings().respect_robots = False
    respx_mock.get("https://demo.sourcehero.local/techwire").mock(return_value=httpx_response(200, rss_feed))
    respx_mock.get("https://demo.sourcehero.local/techwire/hx2-announce").mock(
        return_value=httpx_response(
            200, "<html><body><article><p>Full text version.</p></article></body></html>"
        )
    )
    respx_mock.get("https://demo.sourcehero.local/techwire/orchid-7").mock(
        return_value=httpx_response(500, b"")
    )
    docs = extract_feed("https://demo.sourcehero.local/techwire")
    by_title = {d.title: d for d in docs}
    assert "Halcyon announces HX-2 solid-state cell" in by_title
    # The unreachable entry falls back to its summary rather than vanishing.
    orchid = by_title["Orchid-7 released under an open licence"]
    assert "Orchid-7 has 7 billion parameters." in orchid.text
    assert any(w.startswith("entry_fetch_failed") for w in orchid.warnings)


def test_rss_parse_failure_falls_back_to_element_tree():
    from app.ingestion.rss import _parse

    # feedparser rejects this; ElementTree can still read it.
    assert _parse(b"<rss><channel><item><title>T</title></item></channel></rss>") is not None


# --- versioning ----------------------------------------------------------


def _source(db, url: str = "https://demo.test/doc") -> Source:
    source = Source(user_id=USER, source_type="webpage", name="T", url=url, canonical_url=url)
    db.add(source)
    db.flush()
    return source


def _extracted(url: str, text: str, published=None):
    from app.ingestion.types import ExtractedDocument

    return ExtractedDocument(title="T", text=text, url=url, published_at=published)


def test_first_ingest_is_new(db):
    source = _source(db)
    result = upsert_document_version(db, source=source, extracted=_extracted(source.url, "First body."))
    assert result.status == "new"
    assert result.version.version_no == 1
    assert result.document.current_version_id == result.version.id


def test_identical_content_is_unchanged(db):
    source = _source(db)
    upsert_document_version(db, source=source, extracted=_extracted(source.url, "Same body."))
    db.flush()
    again = upsert_document_version(db, source=source, extracted=_extracted(source.url, "Same body."))
    assert again.status == "unchanged"
    assert again.version is None
    assert db.query(DocumentVersion).count() == 1


def test_changed_content_creates_a_version_with_diff_stats(db):
    from datetime import UTC, datetime

    source = _source(db)
    t0 = datetime(2026, 3, 1, tzinfo=UTC)
    first = upsert_document_version(
        db, source=source, extracted=_extracted(source.url, "A. B. C."), effective_at=t0
    )
    db.flush()
    second = upsert_document_version(
        db,
        source=source,
        extracted=_extracted(source.url, "A. B changed. D."),
        effective_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert second.status == "changed"
    assert second.version.version_no == 2
    assert second.version.diff_stats["sentences_removed"] >= 1
    assert second.version.diff_stats["sentences_added"] >= 1
    assert second.document.current_version_id == second.version.id
    assert first.version.id != second.version.id


def test_backfilling_an_earlier_version_does_not_change_current(db):
    from datetime import UTC, datetime

    source = _source(db)
    live = upsert_document_version(
        db,
        source=source,
        extracted=_extracted(source.url, "Live text from June."),
        effective_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    db.flush()
    archived = upsert_document_version(
        db,
        source=source,
        extracted=_extracted(source.url, "Archived text from January."),
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        archived_at=datetime(2026, 1, 1, tzinfo=UTC),
        origin="wayback",
    )
    assert archived.status == "new"
    # This is the whole point of ordering by effective_at rather than by id.
    assert live.document.current_version_id == live.version.id
    assert archived.document.current_version_id != archived.version.id


def test_empty_content_is_refused(db):
    source = _source(db)
    with pytest.raises(ValueError):
        upsert_document_version(db, source=source, extracted=_extracted(source.url, "   "))


def test_clean_text_is_normalized(db):
    source = _source(db)
    result = upsert_document_version(
        db, source=source, extracted=_extracted(source.url, "Line  one\n\n\n\nPara  two.\x00")
    )
    assert result.version.clean_text == "Line one\n\nPara two."


def test_versions_of_one_document_keep_one_identity(db):
    from datetime import UTC, datetime

    source = _source(db)
    for day in (1, 2):
        upsert_document_version(
            db,
            source=source,
            extracted=_extracted(source.url, f"Body {day}. Something changed here."),
            effective_at=datetime(2026, 5, day, tzinfo=UTC),
        )
    docs = db.query(Document).filter(Document.identity_key == f"url:{source.url}").all()
    assert len(docs) == 1
    assert db.query(DocumentVersion).count() == 2


# --- indexing ------------------------------------------------------------


def test_index_version_writes_chunks_with_exact_offsets(db):
    source = _source(db, "https://demo.test/idx")
    result = upsert_document_version(
        db, source=source, extracted=_extracted(source.url, " ".join(f"Sentence {i}." for i in range(40)))
    )
    from app.indexing.indexer import index_version

    stats = index_version(db, result.version.id)
    assert stats["created"] > 0

    from app.models.chunk import Chunk

    chunks = db.query(Chunk).filter(Chunk.version_id == result.version.id).all()
    assert chunks
    for chunk in chunks:
        assert result.version.clean_text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.embedding is not None
        assert chunk.embedding_model == "hashing-v1"


def test_index_version_is_idempotent(db):
    source = _source(db, "https://demo.test/idx2")
    result = upsert_document_version(db, source=source, extracted=_extracted(source.url, "Body text here."))
    from app.indexing.indexer import index_version
    from app.models.chunk import Chunk

    index_version(db, result.version.id)
    first = db.query(Chunk).filter(Chunk.version_id == result.version.id).count()
    stats = index_version(db, result.version.id)
    assert stats["created"] == 0, "a second index must not create duplicate chunks"
    assert db.query(Chunk).filter(Chunk.version_id == result.version.id).count() == first


# --- paused sources ------------------------------------------------------


def test_paused_source_fails_without_retrying(db, client, make_client):
    """A paused source is a deliberate state; retrying cannot change it."""
    from app.jobs.registry import JobContext

    source = Source(
        user_id=USER,
        source_type="webpage",
        name="P",
        url="https://demo.test/p",
        canonical_url="https://demo.test/p",
        status="paused",
    )
    db.add(source)
    db.flush()
    job = _job_row(db, source.user_id, "ingest_source", {"source_id": str(source.id)})

    from app.ingestion.pipeline import ingest_source_job

    ctx = JobContext(db=db, job=job, user_id=source.user_id)
    with pytest.raises(NonRetryableJobError):
        ingest_source_job(ctx, dict(job.payload))

    # Nothing was attempted, so there is no ingestion run to record -- a pause is a
    # skip, not a failed fetch.
    from app.models.chunk import IngestionRun

    assert db.query(IngestionRun).filter(IngestionRun.source_id == source.id).count() == 0
    # And the source keeps its paused status rather than being marked failed.
    db.refresh(source)
    assert source.status == "paused"
    _ = client, make_client


def _job_row(db, user_id, kind, payload):
    """Insert a jobs row directly, bypassing inline execution."""
    import json

    from sqlalchemy import text

    row = db.execute(
        text(
            """
            INSERT INTO jobs (user_id, kind, payload, status, run_after, attempts, max_attempts)
            VALUES (:uid, :kind, CAST(:payload AS jsonb), 'running', now(), 1, 5)
            RETURNING id
            """
        ),
        {"uid": user_id, "kind": kind, "payload": json.dumps(payload)},
    ).scalar_one()
    db.flush()
    from app.models.job import Job

    return db.get(Job, row)


# --- demo corpus ---------------------------------------------------------


REQUIRED_SENTENCES = {
    "techwire-hx2-announce": [
        "The HX-2 cell delivers an energy density of 450 Wh/kg.",
        "Mass production of the HX-2 will begin in September 2026.",
        "Halcyon Cells expects HX-2 pack prices of $120/kWh at launch.",
    ],
    "greengrid-hx2-lab-test": [
        "Independent lab tests measured the HX-2 energy density at 380 Wh/kg.",
        "The HX-2 retained 92% of its capacity after 1,000 charge cycles.",
    ],
    "halcyon-press-release": [
        "Mass production of the HX-2 will begin in September 2026.",
        "The HX-2 retained 92% of its capacity after 1,000 charge cycles.",
        "Mass production of the HX-2 has been postponed to January 2027.",
    ],
    "analyst-hx2-cost": [
        "First-year HX-2 pack prices will be above $150/kWh.",
        "The HX-2 energy density of 450 Wh/kg would lead the industry.",
    ],
    "zh-hx2-news": [
        "HX-2 电池的能量密度为 450 Wh/kg。",
        "Halcyon Cells 计划于 2026 年 9 月开始量产 HX-2。",
    ],
    "note-hx2-budget": [
        "Mass production of the HX-2 will begin in September 2026, so we should budget for it in Q4.",
    ],
    "ferro-gazette-congestion": [
        "Ferro City's congestion charge reduced downtown traffic by 18% in its first year.",
        "The congestion charge costs drivers $9 per day.",
    ],
    "ferro-council-report": [
        "Downtown traffic fell by 18% after the congestion charge was introduced.",
        "Revised data shows downtown traffic fell by 11% after the congestion charge was introduced.",
    ],
    "ferro-drivers-association": [
        "The congestion charge did not reduce downtown traffic.",
        "The congestion charge costs drivers $12 per day.",
    ],
    "orchid-release-blog": [
        "Orchid-7 has 7 billion parameters.",
        "Orchid-7 was trained on 2 trillion tokens.",
        "Orchid-7 scored 68% on the ReasonBench evaluation.",
    ],
    "orchid-benchmark-review": [
        "Orchid-7 scored 68% on the ReasonBench evaluation.",
        "Orchid-7 was trained on 2 trillion tokens.",
    ],
    "orchid-forum-reproduction": [
        "Orchid-7 scored 61% on the ReasonBench evaluation in our reproduction.",
    ],
}


@pytest.mark.parametrize("corpus_id", sorted(REQUIRED_SENTENCES))
def test_required_sentences_appear_verbatim(corpus_id):
    """The plan pins these sentences; tests across M7-M12 assert on them."""
    corpus = {f.id: f for f in load_corpus()}
    assert corpus_id in corpus, f"{corpus_id} is missing from the demo corpus"
    body = "\n".join(v.body for v in corpus[corpus_id].versions)
    for sentence in REQUIRED_SENTENCES[corpus_id]:
        assert sentence in body, f"{corpus_id} is missing: {sentence!r}"


def test_corpus_shape_matches_the_plan():
    corpus = load_corpus()
    assert len(corpus) == 12, "the plan specifies 12 demo documents"
    assert sum(len(f.versions) for f in corpus) == 14, "the plan specifies 14 versions"
    multi = [f.id for f in corpus if f.has_multiple_versions]
    assert sorted(multi) == ["ferro-council-report", "halcyon-press-release"]


def test_corpus_urls_cannot_be_fetched():
    """Demo URLs must be unresolvable, so a stray fetch is impossible by construction."""
    for corpus_file in load_corpus():
        if corpus_file.url:
            assert "demo.sourcehero.local" in corpus_file.url


def test_corpus_language_detection():
    corpus = {f.id: f for f in load_corpus()}
    assert to_extracted(corpus["zh-hx2-news"], corpus["zh-hx2-news"].versions[0]).lang == "zh"
    assert to_extracted(corpus["orchid-release-blog"], corpus["orchid-release-blog"].versions[0]).lang == "en"


def test_seed_creates_the_expected_shape_and_is_idempotent(db):
    from app.demo.seed import seed_demo, summary
    from app.jobs.handlers import register_all

    register_all()
    from app.core.config import get_settings

    get_settings().jobs_inline = False
    try:
        stats = seed_demo(db, USER)
        assert stats.documents == 12
        assert stats.versions == 14
        counts = summary(db, USER)
        assert counts["sources"] == 12
        db.commit()

        # Running again must change nothing.
        again = seed_demo(db, USER)
        assert again.documents == 12
        assert again.unchanged == 14, "every version should be recognised as unchanged"
        assert summary(db, USER) == counts
    finally:
        get_settings().jobs_inline = True


def test_every_seeded_version_gets_chunks(db):
    from app.core.config import get_settings
    from app.demo.seed import seed_demo
    from app.indexing.indexer import index_version
    from app.jobs.handlers import register_all
    from app.models.chunk import Chunk

    register_all()
    get_settings().jobs_inline = False
    try:
        seed_demo(db, USER)
        db.flush()
        versions = db.query(DocumentVersion).filter(DocumentVersion.user_id == USER).all()
        assert versions
        for version in versions:
            index_version(db, version.id)
        chunks = db.query(Chunk).filter(Chunk.user_id == USER).all()
        assert chunks
        assert {c.version_id for c in chunks} == {v.id for v in versions}
        for chunk in chunks:
            version = db.get(DocumentVersion, chunk.version_id)
            assert version.clean_text[chunk.char_start : chunk.char_end] == chunk.text
    finally:
        get_settings().jobs_inline = True
