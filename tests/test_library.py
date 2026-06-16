import pytest

from tests.conftest import TEST_USER_ID
from app.ingestion.chunking import sha256_text
from app.models import Document, DocumentChunk, Source
from app.retrieval.search import search_documents
from app.services.library import (
    add_collection_item,
    add_item_tag,
    create_collection,
    create_tag,
    list_documents,
)
from app.services.pipeline import delete_source, update_source


def _seed_document(db, source_type="webpage", title="Vector Notes", text="vector search retrieval evaluation"):
    source = Source(user_id=TEST_USER_ID, source_type=source_type, name=f"{source_type} source", url="https://example.com")
    db.add(source)
    db.commit()
    db.refresh(source)
    document = Document(
        source_id=source.id,
        user_id=TEST_USER_ID,
        title=title,
        url=source.url,
        author=None,
        published_at=None,
        content_hash=sha256_text(title, text),
        raw_text=text,
        clean_text=text,
    )
    db.add(document)
    db.flush()
    db.add(
        DocumentChunk(
            document_id=document.id,
            user_id=TEST_USER_ID,
            chunk_index=0,
            chunk_text=text,
            chunk_hash=sha256_text("chunk", title, text),
            token_estimate=10,
            metadata_json="{}",
            embedding_id="test",
        )
    )
    db.commit()
    db.refresh(document)
    return source, document


def test_collection_and_tag_names_are_unique(db_session):
    create_collection(db_session, TEST_USER_ID, "Research")
    create_tag(db_session, TEST_USER_ID, "AI")
    with pytest.raises(ValueError):
        create_collection(db_session, TEST_USER_ID, "Research")
    with pytest.raises(ValueError):
        create_tag(db_session, TEST_USER_ID, "AI")


def test_attach_items_and_filter_documents(db_session):
    source, document = _seed_document(db_session)
    collection = create_collection(db_session, TEST_USER_ID, "RAG")
    tag = create_tag(db_session, TEST_USER_ID, "retrieval")
    add_collection_item(db_session, TEST_USER_ID, collection.id, "source", source.id)
    add_item_tag(db_session, TEST_USER_ID, tag.id, "document", document.id)

    by_collection = list_documents(db_session, TEST_USER_ID, collection_id=collection.id)
    by_tag = list_documents(db_session, TEST_USER_ID, tags=["retrieval"])

    assert [row[0].id for row in by_collection] == [document.id]
    assert [row[0].id for row in by_tag] == [document.id]


def test_search_filters_by_collection_and_tag(db_session):
    source, document = _seed_document(db_session, text="alpha vector retrieval")
    other_source, _ = _seed_document(db_session, title="Other", text="alpha unrelated material")
    collection = create_collection(db_session, TEST_USER_ID, "Selected")
    tag = create_tag(db_session, TEST_USER_ID, "keeper")
    add_collection_item(db_session, TEST_USER_ID, collection.id, "source", source.id)
    add_item_tag(db_session, TEST_USER_ID, tag.id, "source", source.id)

    hits = search_documents(db_session, TEST_USER_ID, "alpha", collection_id=collection.id, tags=["keeper"])

    assert hits
    assert {hit.source_id for hit in hits} == {source.id}
    assert other_source.id not in {hit.source_id for hit in hits}


def test_update_and_delete_source_cleans_library_links(db_session):
    source, document = _seed_document(db_session)
    collection = create_collection(db_session, TEST_USER_ID, "Cleanup")
    tag = create_tag(db_session, TEST_USER_ID, "cleanup")
    add_collection_item(db_session, TEST_USER_ID, collection.id, "source", source.id)
    add_item_tag(db_session, TEST_USER_ID, tag.id, "document", document.id)

    updated = update_source(db_session, TEST_USER_ID, source.id, name="Updated", status="paused")
    delete_source(db_session, TEST_USER_ID, updated.id)

    assert updated.name == "Updated"
    assert list_documents(db_session, TEST_USER_ID, collection_id=collection.id) == []
    assert list_documents(db_session, TEST_USER_ID, tags=["cleanup"]) == []
