"""OPML and highlight CSV imports."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.imports import MAX_IMPORT_BYTES
from app.models.source import Source

pytestmark = pytest.mark.db


def _opml(*outlines: str) -> bytes:
    body = "".join(outlines)
    return (
        '<?xml version="1.0"?><opml version="2.0"><head><title>feeds</title></head>'
        f"<body>{body}</body></opml>"
    ).encode()


def _feed(url: str, text: str = "Feed") -> str:
    return f'<outline type="rss" text="{text}" xmlUrl="{url}"/>'


HIGHLIGHT_CSV_HEADER = "Highlight,Title,Author,URL,Note,Location,Date\n"


def _csv(*rows: str, header: str = HIGHLIGHT_CSV_HEADER) -> bytes:
    return (header + "\n".join(rows) + "\n").encode("utf-8")


def _row(
    highlight: str, *, title: str = "Book", note: str = "", url: str = "https://book.example/ch1"
) -> str:
    return f'"{highlight}","{title}","Ada Writer","{url}","{note}","42","2026-10-01"'


@pytest.fixture
def authed(make_client):
    return make_client(email=f"imp{uuid.uuid4().hex[:10]}@test.local")


def _rss_sources(db, user_id) -> list[Source]:
    return list(
        db.execute(select(Source).where(Source.user_id == user_id, Source.source_type == "rss")).scalars()
    )


def test_opml_creates_sources_and_skips_duplicates(authed, db):
    data = _opml(
        _feed("https://feeds.example.com/a.xml", "Alpha"),
        _feed("HTTPS://FEEDS.example.com/a.xml/", "Alpha again"),
        _feed("https://feeds.example.com/b.xml", "Beta"),
    )
    resp = authed.post("/v1/imports/opml", files={"file": ("subs.opml", data, "text/xml")})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 2, "skipped": 1, "errors": []}

    me = authed.get("/v1/auth/me").json()
    urls = sorted(s.url for s in _rss_sources(db, uuid.UUID(me["id"])))
    assert urls == ["https://feeds.example.com/a.xml", "https://feeds.example.com/b.xml"]

    # A second import of the same file adds nothing and skips what already exists.
    again = authed.post("/v1/imports/opml", files={"file": ("subs.opml", data, "text/xml")})
    assert again.json() == {"created": 0, "skipped": 3, "errors": []}


def test_opml_private_network_url_is_reported_not_created(authed, db):
    data = _opml(
        _feed("http://10.0.0.5/private.xml", "Internal"),
        _feed("https://feeds.example.com/ok.xml", "Public"),
    )
    resp = authed.post("/v1/imports/opml", files={"file": ("subs.opml", data, "text/xml")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["errors"] == [{"code": "ssrf_blocked", "url": "http://10.0.0.5/private.xml", "row": None}]

    me = authed.get("/v1/auth/me").json()
    urls = [s.url for s in _rss_sources(db, uuid.UUID(me["id"]))]
    assert "http://10.0.0.5/private.xml" not in urls
    assert "https://feeds.example.com/ok.xml" in urls


def test_opml_malformed_xml_is_bad_opml(authed):
    resp = authed.post("/v1/imports/opml", files={"file": ("subs.opml", b"<opml><body>", "text/xml")})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "bad_opml"


def test_highlights_csv_missing_note_column_is_bad_csv(authed):
    header = "Highlight,Title,Author,URL,Location,Date\n"
    data = _csv(_row("A line."), header=header)
    resp = authed.post("/v1/imports/highlights", files={"file": ("h.csv", data, "text/csv")})
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "bad_csv"
    assert error["details"]["missing"] == ["Note"]


def test_imported_highlights_are_searchable(authed):
    text = "Compound interest rewards patience more than cleverness."
    data = _csv(_row(text, note="my own gloss", title="Money Book"))
    resp = authed.post("/v1/imports/highlights", files={"file": ("h.csv", data, "text/csv")})
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1

    hits = authed.post("/v1/search", json={"query": "compound interest patience", "mode": "lexical"})
    assert hits.status_code == 200, hits.text
    texts = [hit["text"] for hit in hits.json()["hits"]]
    assert any(text in t for t in texts)


def test_importing_same_highlights_twice_skips_second_run(authed):
    data = _csv(_row("Small habits compound."), _row("Small habits compound.", title="Other edition"))
    first = authed.post("/v1/imports/highlights", files={"file": ("h.csv", data, "text/csv")})
    assert first.json() == {"created": 1, "skipped": 1, "errors": []}

    second = authed.post("/v1/imports/highlights", files={"file": ("h.csv", data, "text/csv")})
    assert second.json() == {"created": 0, "skipped": 2, "errors": []}


def test_file_over_two_mb_is_413(authed):
    assert MAX_IMPORT_BYTES == 2 * 1024 * 1024
    big = b"x" * (MAX_IMPORT_BYTES + 1)
    resp = authed.post("/v1/imports/opml", files={"file": ("big.opml", big, "text/xml")})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"
