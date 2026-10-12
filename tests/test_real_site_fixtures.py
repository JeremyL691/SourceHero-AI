"""Offline checks on the real-site snapshots saved by `tests/test_real_sites.py` (K7.6).

No network. Each test skips with a clear reason if its snapshot has not been
recorded yet (run `uv run pytest -m network tests/test_real_sites.py -o addopts=""`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.ingestion import rss
from app.ingestion.pdf import extract_pdf
from app.ingestion.webpage import _explicit_date, _looks_like_prose, extract_webpage

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "real_sites"
EN_WIKI = "https://en.wikipedia.org/wiki/Lithium-ion_battery"
ZH_WIKI = "https://zh.wikipedia.org/wiki/锂离子电池"
NIST_PDF = "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-12r1.pdf"

_SENTENCE = re.compile(r"[^.!?。！？\n]{10,}[.!?。！？]")


def _snapshot(name: str) -> bytes:
    path = SNAPSHOT_DIR / name
    if not path.exists():
        pytest.skip(f"snapshot {name} not recorded yet; run the -m network live test first")
    return path.read_bytes()


def _sentences(text: str) -> int:
    return len(_SENTENCE.findall(text))


def test_en_wikipedia_snapshot_extracts_article() -> None:
    html = _snapshot("en-wikipedia-lithium-ion.html").decode("utf-8")
    doc = extract_webpage(html, EN_WIKI)
    assert doc.title.strip()
    # Not only navigation: the body must pass the same prose test the extractor uses
    # to reject chrome, and carry a real article's worth of sentences.
    assert _looks_like_prose(doc.text)
    assert len(doc.text) >= 5000
    # Threshold 50: the article has hundreds of sentences; 50 leaves room for
    # extractor variation and still fails on a nav-only or stub extraction.
    assert _sentences(doc.text) >= 50


def test_en_wikipedia_has_no_invented_publication_date() -> None:
    # Wikipedia pages carry no publication date in the markup. The footer's "last
    # edited" line must not be promoted to published_at (it would seed the version
    # timeline with a date the page never states).
    html = _snapshot("en-wikipedia-lithium-ion.html").decode("utf-8")
    doc = extract_webpage(html, EN_WIKI)
    assert _explicit_date(html) is None
    assert doc.published_at is None


def test_zh_wikipedia_snapshot_extracts_article() -> None:
    html = _snapshot("zh-wikipedia-lithium-ion-battery.html").decode("utf-8")
    doc = extract_webpage(html, ZH_WIKI)
    assert doc.title.strip()
    assert doc.lang == "zh"
    assert _looks_like_prose(doc.text)
    assert len(doc.text) >= 2000
    # Threshold 30: the Chinese article has well over 100 full sentences; 30 is a
    # floor a navigation-only extraction cannot reach.
    assert _sentences(doc.text) >= 30


def test_us_government_rss_snapshot_parses_entries() -> None:
    content = _snapshot("nasa-news.rss")
    entries = rss._entries(rss._parse(content))
    assert len(entries) >= 1
    for entry in entries:
        assert (rss._first(entry, "title") or "").strip()
        assert rss._first(entry, "link")
    # Each item's pubDate is visible in the snapshot, so a date must parse.
    dated = [e for e in entries if rss._entry_time(e) is not None]
    assert dated, "feed items carry pubDate, so at least one date must parse"


def test_us_government_pdf_snapshot_has_page_map() -> None:
    data = _snapshot("usgov-nist-sp800-12r1.pdf")
    doc = extract_pdf(data, url=NIST_PDF)
    assert doc.title.strip()
    assert len(doc.text) >= 10000
    assert doc.page_map, "a PDF must carry a page map"
    numbers = [p["page"] for p in doc.page_map]
    assert numbers == sorted(numbers) and numbers[0] >= 1
    # Offsets must cover the text they point at.
    last = doc.page_map[-1]
    assert last["end"] <= len(doc.text)
    assert "no_text_layer" not in doc.warnings
