"""Sources and documents over HTTP, plus the tenant-isolation cases they add."""

from __future__ import annotations

import uuid

import pytest

from app.ingestion.types import ExtractedDocument
from app.ingestion.versions import upsert_document_version

pytestmark = pytest.mark.db


@pytest.fixture
def authed(make_client):
    return make_client(email=f"src{uuid.uuid4().hex[:10]}@test.local")


def _create_source(client, **overrides):
    payload = {
        "source_type": "webpage",
        "name": "Test source",
        "url": "https://demo.test/article",
    }
    payload.update(overrides)
    resp = client.post("/v1/sources", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- sources -------------------------------------------------------------


def test_create_and_list_sources(authed):
    created = _create_source(authed)
    assert created["canonical_url"] == "https://demo.test/article"
    listed = authed.get("/v1/sources").json()
    assert [s["id"] for s in listed] == [created["id"]]


def test_creating_the_same_url_twice_reuses_the_source(authed):
    first = _create_source(authed)
    # Tracking parameters must not create a second document of the same page.
    second = _create_source(authed, url="https://demo.test/article?utm_source=newsletter")
    assert second["id"] == first["id"]


def test_webpage_source_requires_a_url(authed):
    resp = authed.post("/v1/sources", json={"source_type": "webpage", "name": "No url"})
    assert resp.status_code == 422, resp.text


def test_unknown_source_type_is_rejected(authed):
    resp = authed.post("/v1/sources", json={"source_type": "telepathy", "name": "x"})
    assert resp.status_code == 422, resp.text


def test_update_and_delete(authed):
    created = _create_source(authed)
    patched = authed.patch(f"/v1/sources/{created['id']}", json={"name": "Renamed", "watch": True})
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["watch"] is True

    assert authed.delete(f"/v1/sources/{created['id']}").status_code == 204
    assert authed.get(f"/v1/sources/{created['id']}").status_code == 404


def test_refresh_queues_a_job(authed, no_inline_jobs):
    created = _create_source(authed)
    resp = authed.post(f"/v1/sources/{created['id']}/refresh")
    assert resp.status_code == 202, resp.text
    assert resp.json()["job_id"]


def test_wayback_backfill_queues_a_job(authed, no_inline_jobs):
    created = _create_source(authed)
    resp = authed.post(f"/v1/sources/{created['id']}/wayback", params={"limit": 4, "from": "20260101"})
    assert resp.status_code == 202, resp.text


def test_upload_creates_a_source(authed, tmp_path, monkeypatch, no_inline_jobs):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    resp = authed.post(
        "/v1/sources/upload",
        files={"file": ("notes.md", b"# Heading\n\nA sentence about HX-2.", "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_type"] == "file"


def test_upload_rejects_unsupported_types(authed):
    resp = authed.post(
        "/v1/sources/upload", files={"file": ("bad.exe", b"MZ...", "application/octet-stream")}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "bad_file_type"


# --- documents -----------------------------------------------------------


def _seed_document(authed, db, *, text="The HX-2 cell delivers 450 Wh/kg.", versions=1, index=False):
    """Create one document with `versions` versions, optionally indexing them.

    Indexing is opt-in because most document tests do not care about chunks, but a
    search test cannot find anything that was never chunked.
    """
    from datetime import UTC, datetime, timedelta

    source = _create_source(authed)
    made = []
    for i in range(versions):
        extracted = ExtractedDocument(
            title="Doc",
            text=f"{text} Version {i} adds another sentence here.",
            url=source["url"],
        )
        made.append(
            upsert_document_version(
                db,
                source=_db_source(db, source["id"]),
                extracted=extracted,
                effective_at=datetime(2026, 3, 1, tzinfo=UTC) + timedelta(days=30 * i),
            )
        )
    db.commit()
    if index:
        from app.indexing.indexer import index_version

        for result in made:
            if result.version is not None:
                index_version(db, result.version.id)
        db.commit()
    return source


def _db_source(db, source_id):
    from app.models.source import Source

    return db.get(Source, uuid.UUID(source_id))


def test_document_list_and_detail(authed, db):
    _seed_document(authed, db)
    listed = authed.get("/v1/documents").json()
    assert len(listed) == 1
    document_id = listed[0]["id"]

    detail = authed.get(f"/v1/documents/{document_id}").json()
    assert "450 Wh/kg" in detail["text"]
    assert detail["version_count"] == 1


def test_document_versions_and_diff(authed, db):
    _seed_document(authed, db, versions=2)
    document_id = authed.get("/v1/documents").json()[0]["id"]
    versions = authed.get(f"/v1/documents/{document_id}/versions").json()
    assert len(versions) == 2
    assert versions[0]["effective_at"] < versions[1]["effective_at"]

    diff = authed.get(
        f"/v1/documents/{document_id}/diff",
        params={"from": versions[0]["id"], "to": versions[1]["id"]},
    )
    assert diff.status_code == 200, diff.text
    assert "Version 1" in diff.json()["diff"]


def test_version_of_another_document_is_not_found(authed, db):
    _seed_document(authed, db)
    document_id = authed.get("/v1/documents").json()[0]["id"]
    assert authed.get(f"/v1/documents/{document_id}/versions/999999").status_code == 404


def test_unknown_document_is_404(authed):
    assert authed.get(f"/v1/documents/{uuid.uuid4()}").status_code == 404


# --- tenant isolation ----------------------------------------------------


def test_sources_are_isolated_between_users(authed, make_client, db):
    created = _create_source(authed)

    other = make_client(email=f"other{uuid.uuid4().hex[:10]}@test.local")
    assert other.get("/v1/sources").json() == []
    assert other.get(f"/v1/sources/{created['id']}").status_code == 404
    assert other.patch(f"/v1/sources/{created['id']}", json={"name": "stolen"}).status_code == 404
    assert other.delete(f"/v1/sources/{created['id']}").status_code == 404


def test_documents_are_isolated_between_users(authed, make_client, db):
    _seed_document(authed, db)
    document_id = authed.get("/v1/documents").json()[0]["id"]

    other = make_client(email=f"other{uuid.uuid4().hex[:10]}@test.local")
    assert other.get("/v1/documents").json() == []
    assert other.get(f"/v1/documents/{document_id}").status_code == 404
    assert other.get(f"/v1/documents/{document_id}/versions").status_code == 404


def test_ingestion_runs_are_isolated(make_client, authed, db):
    other = make_client(email=f"other{uuid.uuid4().hex[:10]}@test.local")
    _seed_document(authed, db)
    # The other user sees no run history.
    assert other.get("/v1/ingestion-runs").json() == []


# --- search --------------------------------------------------------------


def test_search_endpoint_returns_scored_hits(authed, db):
    _seed_document(authed, db, text="The HX-2 cell delivers an energy density of 450 Wh/kg.", index=True)
    resp = authed.post("/v1/search", json={"query": "energy density", "k": 3})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["hits"], resp.text
    hit = body["hits"][0]
    assert set(hit["scores"]) == {"lexical_rank", "vector_rank", "rrf", "rerank"}
    assert hit["char_start"] < hit["char_end"]
    assert "timings_ms" in body and body["timings_ms"]["total"] >= 0


def test_search_rejects_an_empty_query(authed):
    assert authed.post("/v1/search", json={"query": ""}).status_code == 422


def test_search_never_crosses_tenants(authed, make_client, db):
    _seed_document(authed, db, text="The HX-2 cell delivers an energy density of 450 Wh/kg.", index=True)
    other = make_client(email=f"other{uuid.uuid4().hex[:10]}@test.local")
    assert other.post("/v1/search", json={"query": "energy density"}).json()["hits"] == []
