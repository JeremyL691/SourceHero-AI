"""POST /v1/captures/parse: previews never store or enqueue anything."""

from __future__ import annotations

import uuid

import pytest

from app.ingestion.sources import MAX_UPLOAD_BYTES

pytestmark = pytest.mark.db

TEXT = (
    "The HX-2 cell delivers an energy density of 450 Wh/kg. "
    "Mass production of the HX-2 will begin in September 2026. "
    "Is this real? "
    "Thanks for reading."
)


@pytest.fixture
def authed(make_client):
    return make_client(email=f"preview{uuid.uuid4().hex[:10]}@test.local")


def _counts(client) -> tuple[int, int]:
    documents = len(client.get("/v1/documents", params={"limit": 200}).json())
    sources = len(client.get("/v1/sources", params={"limit": 200}).json())
    return documents, sources


def test_text_preview_counts_sentences_and_claims(authed):
    resp = authed.post("/v1/captures/parse", json={"text": TEXT})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "text"
    assert body["sentences"] == 4
    # Two assertive statements; the question and the sign-off are not claims.
    assert body["candidate_claims"] == 2
    # A single-line note has no heading, so the guess is its opening 120 characters.
    assert body["title_guess"] == TEXT[:120]
    assert body["preview"].startswith("The HX-2 cell delivers")


def test_text_preview_does_not_store_or_enqueue(authed):
    before = _counts(authed)
    assert authed.post("/v1/captures/parse", json={"text": TEXT}).status_code == 200
    assert _counts(authed) == before


def test_pdf_preview_extracts_text_without_storing(authed, pdf_two_pages):
    before = _counts(authed)
    resp = authed.post(
        "/v1/captures/parse",
        files={"file": ("halcyon.pdf", pdf_two_pages, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "file"
    assert body["filename"] == "halcyon.pdf"
    assert body["pages"] == 2
    assert body["chars"] > 0
    assert "450 Wh/kg" in body["preview"]
    assert "Mass production" in body["preview"]
    assert body["warnings"] == []
    assert _counts(authed) == before, "a preview must not create a source or a document"


def test_markdown_preview_uses_first_heading_as_title(authed):
    resp = authed.post(
        "/v1/captures/parse",
        files={"file": ("note.md", b"# Battery notes\n\nHX-2 is a cell.", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Battery notes"


def test_unsupported_file_type_is_rejected(authed):
    resp = authed.post(
        "/v1/captures/parse",
        files={"file": ("bad.exe", b"MZ...", "application/octet-stream")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "bad_file_type"


def test_file_over_the_upload_limit_is_413(authed):
    too_big = b"a" * (MAX_UPLOAD_BYTES + 1)
    before = _counts(authed)
    resp = authed.post("/v1/captures/parse", files={"file": ("big.txt", too_big, "text/plain")})
    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["code"] == "file_too_large"
    assert _counts(authed) == before
