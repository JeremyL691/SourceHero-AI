"""GET /v1/documents filters, and the tags/collections/source_type it returns."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.ingestion.sources import create_source
from app.ingestion.types import ExtractedDocument
from app.ingestion.versions import upsert_document_version
from app.library.service import (
    add_to_collection,
    attach_tag,
    create_collection,
    create_tag,
    set_document_flags,
)

pytestmark = pytest.mark.db


def _me(client) -> uuid.UUID:
    return uuid.UUID(client.get("/v1/auth/me").json()["id"])


def _seed(db, user_id: uuid.UUID, *, source_type: str, title: str, url: str | None = None) -> uuid.UUID:
    source = create_source(
        db,
        user_id,
        source_type=source_type,
        name=title,
        url=url,
    )
    result = upsert_document_version(
        db,
        source=source,
        extracted=ExtractedDocument(title=title, text=f"{title} body about the HX-2 cell.", url=url),
        effective_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    db.commit()
    return result.document.id


@pytest.fixture
def library(make_client, db):
    """Three documents of two source types, with one tag, one collection and one star."""
    client = make_client(email=f"filters{uuid.uuid4().hex[:10]}@test.local")
    user_id = _me(client)
    web = _seed(db, user_id, source_type="webpage", title="Halcyon press", url="https://demo.test/halcyon")
    note = _seed(db, user_id, source_type="note", title="Battery meeting")
    archived_note = _seed(db, user_id, source_type="note", title="Old draft")

    tag = create_tag(db, user_id, name="chemistry")
    collection = create_collection(db, user_id, name="Reading", description=None)
    attach_tag(db, user_id, web, tag.id)
    attach_tag(db, user_id, note, tag.id)
    add_to_collection(db, user_id, collection.id, web)
    set_document_flags(db, user_id, web, starred=True, archived=None)
    set_document_flags(db, user_id, archived_note, starred=None, archived=True)
    return {
        "client": client,
        "user_id": user_id,
        "web": web,
        "note": note,
        "archived_note": archived_note,
        "tag": tag,
        "collection": collection,
    }


def _ids(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {d["id"] for d in resp.json()}


def test_no_filter_returns_everything_with_source_type(library):
    client = library["client"]
    rows = client.get("/v1/documents").json()
    assert len(rows) == 3
    by_id = {r["id"]: r for r in rows}
    assert by_id[str(library["web"])]["source_type"] == "webpage"
    assert by_id[str(library["note"])]["source_type"] == "note"
    assert by_id[str(library["web"])]["tags"] == [{"id": library["tag"].id, "name": "chemistry"}]
    assert by_id[str(library["web"])]["collections"] == [{"id": library["collection"].id, "name": "Reading"}]
    assert by_id[str(library["archived_note"])]["tags"] == []
    assert by_id[str(library["archived_note"])]["collections"] == []


def test_source_type_filter(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"source_type": "webpage"})) == {str(library["web"])}


def test_tag_filter(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"tag_id": library["tag"].id})) == {
        str(library["web"]),
        str(library["note"]),
    }


def test_collection_filter(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"collection_id": library["collection"].id})) == {
        str(library["web"])
    }


def test_starred_filter(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"starred": True})) == {str(library["web"])}
    assert _ids(client.get("/v1/documents", params={"starred": False})) == {
        str(library["note"]),
        str(library["archived_note"]),
    }


def test_archived_filter(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"archived": True})) == {str(library["archived_note"])}
    assert _ids(client.get("/v1/documents", params={"archived": False})) == {
        str(library["web"]),
        str(library["note"]),
    }


def test_text_search_matches_title_or_url(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"q": "HALCYON"})) == {str(library["web"])}
    assert _ids(client.get("/v1/documents", params={"q": "demo.test"})) == {str(library["web"])}
    assert _ids(client.get("/v1/documents", params={"q": "meeting"})) == {str(library["note"])}


def test_text_search_treats_percent_literally(library):
    client = library["client"]
    assert _ids(client.get("/v1/documents", params={"q": "%"})) == set()


def test_text_search_treats_underscore_literally(library):
    client = library["client"]
    # Unescaped, "_" would match any single character and return every document.
    assert _ids(client.get("/v1/documents", params={"q": "_"})) == set()


def test_combined_filters_intersect(library):
    client = library["client"]
    resp = client.get(
        "/v1/documents",
        params={
            "source_type": "note",
            "tag_id": library["tag"].id,
            "starred": False,
            "archived": False,
            "q": "meeting",
            "limit": 10,
            "offset": 0,
        },
    )
    assert _ids(resp) == {str(library["note"])}
    # Each condition alone matches more than the intersection, so the combination is doing work.
    assert _ids(client.get("/v1/documents", params={"source_type": "note"})) == {
        str(library["note"]),
        str(library["archived_note"]),
    }


def test_another_tenants_tag_id_returns_an_empty_list(library, make_client, db):
    stranger = make_client(email=f"stranger{uuid.uuid4().hex[:10]}@test.local")
    stranger_tag = create_tag(db, _me(stranger), name="theirs")
    assert _ids(library["client"].get("/v1/documents", params={"tag_id": stranger_tag.id})) == set()


def test_another_tenants_collection_id_returns_an_empty_list(library, make_client, db):
    stranger = make_client(email=f"stranger{uuid.uuid4().hex[:10]}@test.local")
    stranger_collection = create_collection(db, _me(stranger), name="Theirs", description=None)
    assert (
        _ids(library["client"].get("/v1/documents", params={"collection_id": stranger_collection.id}))
        == set()
    )


def test_pagination_applies_after_filters(library):
    client = library["client"]
    page = client.get("/v1/documents", params={"starred": False, "limit": 1, "offset": 0}).json()
    rest = client.get("/v1/documents", params={"starred": False, "limit": 1, "offset": 1}).json()
    assert len(page) == 1 and len(rest) == 1
    assert page[0]["id"] != rest[0]["id"]
