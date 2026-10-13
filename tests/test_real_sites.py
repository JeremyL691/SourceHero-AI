"""Live ingestion smoke test against five real public sites.

Network-only (`-m network`). Each URL goes through the app's own `fetch` and the
matching extractor, and the structure of the result is asserted. Snapshots of the
raw bytes are saved to `tests/fixtures/real_sites/` for items whose licence is
explicit in the bytes themselves (Wikipedia: CC BY-SA 4.0; NIST publication: "not
subject to copyright in the United States"). The news article is never saved.

The offline counterpart is `tests/test_real_site_fixtures.py`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from app.ingestion import rss
from app.ingestion.fetch import fetch
from app.ingestion.pdf import extract_pdf
from app.ingestion.webpage import extract_webpage

pytestmark = pytest.mark.network

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "real_sites"

EN_WIKI = "https://en.wikipedia.org/wiki/Lithium-ion_battery"
ZH_WIKI = "https://zh.wikipedia.org/wiki/锂离子电池"
NASA_RSS = "https://www.nasa.gov/news-release/feed/"
NIST_PDF = "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-12r1.pdf"
NEWS_ARTICLE = "https://www.npr.org/2026/01/07/nx-s1-5667021/dietary-guidelines-rfk-jr-nutrition"

SNAPSHOT_EN = "en-wikipedia-lithium-ion.html"
SNAPSHOT_ZH = "zh-wikipedia-lithium-ion-battery.html"
SNAPSHOT_RSS = "nasa-news.rss"
SNAPSHOT_PDF = "usgov-nist-sp800-12r1.pdf"

# Licence markers that must be present in the bytes before a snapshot is written.
EN_LICENCE = b"Attribution-ShareAlike 4.0 License"
ZH_LICENCE = "知识共享 署名-相同方式共享 4.0协议".encode()
PDF_LICENCE = b"not subject to copyright in the United States"
RSS_LICENCE = b"public domain"

_SENTENCE = re.compile(r"[^.!?。！？\n]{10,}[.!?。！？]")


def _sentence_count(text: str) -> int:
    return len(_SENTENCE.findall(text))


def _save_if_licensed(name: str, data: bytes, marker: bytes) -> None:
    """Write a snapshot only when its own bytes state the licence.

    A committed snapshot is kept unless RECORD_REAL_SITES=1, so a plain `-m network`
    run (and `make test-all`) leaves the working tree clean.
    """
    path = SNAPSHOT_DIR / name
    if path.exists() and os.environ.get("RECORD_REAL_SITES") != "1":
        return
    if marker in data:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _assert_webpage_structure(doc, *, min_chars: int, min_sentences: int) -> None:
    assert doc.title.strip(), "title must not be empty"
    assert len(doc.text) >= min_chars, f"body too short: {len(doc.text)} chars"
    assert _sentence_count(doc.text) >= min_sentences, "body has too few sentences"
    assert "no_text_found" not in doc.warnings


@pytest.mark.network
def test_live_en_wikipedia_article() -> None:
    result = fetch(EN_WIKI)
    assert result.status == 200
    assert result.content_type and result.content_type.startswith("text/html")
    _save_if_licensed(SNAPSHOT_EN, result.content, EN_LICENCE)

    doc = extract_webpage(result.content.decode("utf-8"), EN_WIKI)
    # The article runs to thousands of sentences; 50 is a floor that navigation or a
    # stub could not pass.
    _assert_webpage_structure(doc, min_chars=5000, min_sentences=50)


@pytest.mark.network
def test_live_zh_wikipedia_article() -> None:
    result = fetch(ZH_WIKI)
    assert result.status == 200
    _save_if_licensed(SNAPSHOT_ZH, result.content, ZH_LICENCE)

    doc = extract_webpage(result.content.decode("utf-8"), ZH_WIKI)
    assert doc.title.strip()
    assert len(doc.text) >= 2000
    # Chinese sentences end in 。 and ！ / ？, which _SENTENCE also counts.
    assert _sentence_count(doc.text) >= 30
    assert doc.lang == "zh"


@pytest.mark.network
def test_live_us_government_rss_feed() -> None:
    result = fetch(NASA_RSS)
    assert result.status == 200
    assert result.content.lstrip().startswith(b"<?xml") or b"<rss" in result.content[:500]
    _save_if_licensed(SNAPSHOT_RSS, result.content, RSS_LICENCE)

    feed = rss._parse(result.content)
    entries = rss._entries(feed)
    assert len(entries) >= 1
    for entry in entries[:10]:
        assert (rss._first(entry, "title") or "").strip()
        assert rss._first(entry, "link")


@pytest.mark.network
def test_live_us_government_pdf_public_domain() -> None:
    result = fetch(NIST_PDF)
    assert result.status == 200
    assert result.content.startswith(b"%PDF-")
    _save_if_licensed(SNAPSHOT_PDF, result.content, PDF_LICENCE)

    doc = extract_pdf(result.content, url=NIST_PDF)
    assert doc.title.strip()
    assert len(doc.text) >= 10000
    assert doc.page_map and len(doc.page_map) >= 50
    assert "no_text_layer" not in doc.warnings


@pytest.mark.network
def test_live_news_article_structure() -> None:
    # Deliberately not saved: a news page is copyrighted, so only its structure is
    # asserted, live.
    result = fetch(NEWS_ARTICLE)
    assert result.status == 200
    doc = extract_webpage(result.content.decode("utf-8", errors="replace"), NEWS_ARTICLE)
    _assert_webpage_structure(doc, min_chars=800, min_sentences=5)
