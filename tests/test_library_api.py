"""Collections, tags and document flags over HTTP, including tenant isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.ingestion.sources import create_source
from app.ingestion.types import ExtractedDocument
from app.ingestion.versions import upsert_document_version
from app.models.library import ItemTag

pytestmark = pytest.mark.db


def _me(client) -> uuid.UUID:
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["id"])


def _seed_doc(db, user_id: uuid.UUID, title: str) -> uuid.UUID:
    source = create_source(db, user_id, source_type="note", name=title)
    result = upsert_document_version(
        db,
        source=source,
        extracted=ExtractedDocument(title=title, text=f"{title} says the HX-2 cell delivers 450 Wh/kg."),
        effective_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    db.commit()
    return result.document.id


@pytest.fixture
def owner(make_client, db):
    client = make_client(email=f"lib{uuid.uuid4().hex[:10]}@test.local")
    return client, _me(client)


@pytest.fixture
def stranger(make_client):
    client = make_client(email=f"other{uuid.uuid4().hex[:10]}@test.local")
    return client, _me(client)


# --- collections ---------------------------------------------------------


def test_collection_crud(owner):
    client, _ = owner
    created = client.post("/v1/collections", json={"name": "Batteries", "description": "cells"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Batteries"
    assert body["description"] == "cells"
    assert body["document_count"] == 0
    cid = body["id"]

    listed = client.get("/v1/collections").json()
    assert [c["id"] for c in listed] == [cid]

    detail = client.get(f"/v1/collections/{cid}").json()
    assert detail["name"] == "Batteries" and detail["document_count"] == 0

    renamed = client.patch(f"/v1/collections/{cid}", json={"name": "Cells"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Cells"
    assert renamed.json()["description"] == "cells", "PATCH must leave unsent fields alone"

    assert client.delete(f"/v1/collections/{cid}").status_code == 204
    assert client.get(f"/v1/collections/{cid}").status_code == 404
    assert client.get("/v1/collections").json() == []


def test_duplicate_collection_name_is_a_conflict(owner):
    client, _ = owner
    assert client.post("/v1/collections", json={"name": "Dup"}).status_code == 201
    resp = client.post("/v1/collections", json={"name": "Dup"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "name_taken"


def test_collection_items_add_count_and_remove(owner, db):
    client, user_id = owner
    cid = client.post("/v1/collections", json={"name": "Reading"}).json()["id"]
    doc_id = _seed_doc(db, user_id, "Halcyon note")

    assert client.post(f"/v1/collections/{cid}/items", json={"document_id": str(doc_id)}).status_code == 204
    # Adding the same document twice is a no-op, not a second row.
    assert client.post(f"/v1/collections/{cid}/items", json={"document_id": str(doc_id)}).status_code == 204
    assert client.get(f"/v1/collections/{cid}").json()["document_count"] == 1

    docs = client.get("/v1/documents", params={"collection_id": cid}).json()
    assert [d["id"] for d in docs] == [str(doc_id)]
    assert docs[0]["collections"] == [{"id": cid, "name": "Reading"}]

    assert (
        client.request(
            "DELETE", f"/v1/collections/{cid}/items", json={"document_id": str(doc_id)}
        ).status_code
        == 204
    )
    assert client.get(f"/v1/collections/{cid}").json()["document_count"] == 0


def test_adding_another_tenants_document_is_not_found(owner, stranger, db):
    client, _ = owner
    _, stranger_id = stranger
    cid = client.post("/v1/collections", json={"name": "Mine"}).json()["id"]
    foreign_doc = _seed_doc(db, stranger_id, "Their note")

    resp = client.post(f"/v1/collections/{cid}/items", json={"document_id": str(foreign_doc)})
    assert resp.status_code == 404, resp.text
    assert client.get(f"/v1/collections/{cid}").json()["document_count"] == 0


def test_another_tenants_collection_is_not_found(owner, stranger, db):
    client, user_id = owner
    stranger_client, _ = stranger
    cid = client.post("/v1/collections", json={"name": "Private"}).json()["id"]
    my_doc = _seed_doc(db, user_id, "My note")

    assert stranger_client.get(f"/v1/collections/{cid}").status_code == 404
    assert stranger_client.patch(f"/v1/collections/{cid}", json={"name": "x"}).status_code == 404
    assert stranger_client.delete(f"/v1/collections/{cid}").status_code == 404
    resp = stranger_client.post(f"/v1/collections/{cid}/items", json={"document_id": str(my_doc)})
    assert resp.status_code == 404
    assert client.get(f"/v1/collections/{cid}").json()["name"] == "Private"


# --- tags ----------------------------------------------------------------


def test_tag_create_list_and_duplicate(owner):
    client, _ = owner
    created = client.post("/v1/tags", json={"name": "chemistry"})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "chemistry"

    assert client.post("/v1/tags", json={"name": "chemistry"}).status_code == 409
    assert [t["name"] for t in client.get("/v1/tags").json()] == ["chemistry"]


def test_tagging_a_document_and_deleting_the_tag_cascades(owner, db):
    client, user_id = owner
    tag_id = client.post("/v1/tags", json={"name": "priority"}).json()["id"]
    doc_id = _seed_doc(db, user_id, "Tagged note")

    assert client.post(f"/v1/documents/{doc_id}/tags", json={"tag_id": tag_id}).status_code == 204
    assert client.post(f"/v1/documents/{doc_id}/tags", json={"tag_id": tag_id}).status_code == 204
    listed = client.get("/v1/documents").json()
    assert listed[0]["tags"] == [{"id": tag_id, "name": "priority"}]

    assert (
        client.request("DELETE", f"/v1/documents/{doc_id}/tags", json={"tag_id": tag_id}).status_code == 204
    )
    assert client.get("/v1/documents").json()[0]["tags"] == []

    client.post(f"/v1/documents/{doc_id}/tags", json={"tag_id": tag_id})
    assert client.delete(f"/v1/tags/{tag_id}").status_code == 204
    assert client.get("/v1/tags").json() == []
    assert db.query(ItemTag).filter(ItemTag.tag_id == tag_id).count() == 0, "item_tags must cascade"
    assert client.get("/v1/documents").json()[0]["tags"] == []


def test_another_tenants_tag_is_not_found(owner, stranger, db):
    client, user_id = owner
    stranger_client, _ = stranger
    tag_id = stranger_client.post("/v1/tags", json={"name": "theirs"}).json()["id"]
    my_doc = _seed_doc(db, user_id, "Mine")

    assert client.post(f"/v1/documents/{my_doc}/tags", json={"tag_id": tag_id}).status_code == 404
    assert client.delete(f"/v1/tags/{tag_id}").status_code == 404
    assert [t["name"] for t in stranger_client.get("/v1/tags").json()] == ["theirs"]


def test_tagging_another_tenants_document_is_not_found(owner, stranger, db):
    client, _ = owner
    _, stranger_id = stranger
    tag_id = client.post("/v1/tags", json={"name": "mine"}).json()["id"]
    foreign_doc = _seed_doc(db, stranger_id, "Theirs")

    assert client.post(f"/v1/documents/{foreign_doc}/tags", json={"tag_id": tag_id}).status_code == 404


# --- flags ---------------------------------------------------------------


def test_flags_patch_sets_only_the_sent_fields(owner, db):
    client, user_id = owner
    doc_id = _seed_doc(db, user_id, "Flag me")

    starred = client.patch(f"/v1/documents/{doc_id}/flags", json={"starred": True})
    assert starred.status_code == 200, starred.text
    assert starred.json() == {"document_id": str(doc_id), "starred": True, "archived": False}

    both = client.patch(f"/v1/documents/{doc_id}/flags", json={"archived": True})
    assert both.json() == {"document_id": str(doc_id), "starred": True, "archived": True}

    listed = client.get("/v1/documents", params={"starred": True}).json()
    assert [d["id"] for d in listed] == [str(doc_id)]


def test_flags_on_another_tenants_document_are_not_found(owner, stranger, db):
    client, _ = owner
    _, stranger_id = stranger
    foreign_doc = _seed_doc(db, stranger_id, "Not yours")
    assert client.patch(f"/v1/documents/{foreign_doc}/flags", json={"starred": True}).status_code == 404
