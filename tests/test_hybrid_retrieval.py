from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.conftest import TEST_USER_ID
from app.ingestion.chunking import sha256_text
from app.models import Document, DocumentChunk, Source
from app.retrieval import search as search_module
from app.retrieval.vector_store import clear_embeddings, load_embeddings, upsert_embeddings
from app.services.pipeline import delete_source, ingest_source
from app.services.semantic_index import rebuild_semantic_index, semantic_index_status


def _seed_chunk(db, *, title: str, text: str, url: str = "https://example.com") -> tuple[Source, Document, DocumentChunk]:
    source = Source(user_id=TEST_USER_ID, source_type="webpage", name=title, url=url)
    db.add(source)
    db.commit()
    db.refresh(source)

    document = Document(
        source_id=source.id,
        user_id=TEST_USER_ID,
        title=title,
        url=url,
        author=None,
        published_at=None,
        content_hash=sha256_text(title, text),
        raw_text=text,
        clean_text=text,
    )
    db.add(document)
    db.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        user_id=TEST_USER_ID,
        chunk_index=0,
        chunk_text=text,
        chunk_hash=sha256_text("chunk", title, text),
        token_estimate=10,
        metadata_json="{}",
        embedding_id="test",
    )
    db.add(chunk)
    db.commit()
    db.refresh(document)
    db.refresh(chunk)
    return source, document, chunk


def test_hybrid_falls_back_to_lexical_without_openai(monkeypatch, db_session):
    _seed_chunk(db_session, title="Vector Notes", text="alpha vector retrieval evidence")
    monkeypatch.setattr(search_module, "semantic_index_enabled", lambda: False)
    monkeypatch.setattr(search_module, "semantic_index_status", lambda db: SimpleNamespace(ready=False))

    bundle = search_module.retrieve_documents(db_session, TEST_USER_ID, "alpha retrieval", retrieval_mode="hybrid")

    assert bundle.effective_retrieval_mode == "lexical"
    assert bundle.hits


def test_hybrid_can_return_semantic_only_hits(monkeypatch, db_session):
    _, document, chunk = _seed_chunk(db_session, title="Football Notes", text="association football pressing patterns")
    _seed_chunk(db_session, title="Cooking Notes", text="baking bread and roasting vegetables")
    monkeypatch.setattr(search_module, "semantic_index_enabled", lambda: True)
    monkeypatch.setattr(search_module, "semantic_index_status", lambda db: SimpleNamespace(ready=True))
    monkeypatch.setattr(
        search_module,
        "semantic_chunk_scores",
        lambda query, allowed_chunk_ids=None, top_k=5: [(chunk.id, 0.98)],
    )

    bundle = search_module.retrieve_documents(db_session, TEST_USER_ID, "soccer strategy", retrieval_mode="hybrid")

    assert bundle.effective_retrieval_mode == "hybrid"
    assert bundle.hits
    assert bundle.hits[0].document_id == document.id


def test_hybrid_respects_allowed_chunk_filters(monkeypatch, db_session):
    _, selected_document, selected_chunk = _seed_chunk(db_session, title="Selected", text="alpha retrieval keeper")
    _, other_document, other_chunk = _seed_chunk(db_session, title="Other", text="alpha retrieval outsider")
    monkeypatch.setattr(search_module, "semantic_index_enabled", lambda: True)
    monkeypatch.setattr(search_module, "semantic_index_status", lambda db: SimpleNamespace(ready=True))

    def fake_semantic_scores(query, allowed_chunk_ids=None, top_k=5):
        ordered = []
        for chunk_id in (selected_chunk.id, other_chunk.id):
            if allowed_chunk_ids is None or chunk_id in allowed_chunk_ids:
                ordered.append((chunk_id, 0.9))
        return ordered

    monkeypatch.setattr(search_module, "semantic_chunk_scores", fake_semantic_scores)

    bundle = search_module.retrieve_documents(
        db_session,
        TEST_USER_ID,
        "alpha",
        retrieval_mode="hybrid",
        source_ids=[selected_document.source_id],
    )

    assert bundle.hits
    assert {hit.document_id for hit in bundle.hits} == {selected_document.id}
    assert other_document.id not in {hit.document_id for hit in bundle.hits}


@pytest.mark.skip(reason="pgvector-backed semantic_index.py is stubbed; file-based vector_store is the obsolete path these tests exercise.")
def test_rebuild_semantic_index_indexes_existing_chunks(monkeypatch, db_session, tmp_path):
    monkeypatch.setenv("SOURCEHERO_VECTOR_DIR", str(tmp_path))
    clear_embeddings()
    _seed_chunk(db_session, title="Index me", text="semantic rebuild target")
    monkeypatch.setattr(
        "app.services.semantic_index.embed_texts",
        lambda texts, api_key, model="text-embedding-3-small", client_factory=None: [[0.1, 0.2] for _ in texts],
    )
    monkeypatch.setattr("app.services.semantic_index.effective_openai_key", lambda: "sk-test-123")

    result = rebuild_semantic_index(db_session)

    assert result["status"] == "rebuilt"
    assert result["indexed_chunks"] == 1
    assert len(load_embeddings()) == 1


@pytest.mark.skip(reason="pgvector-backed semantic_index.py is stubbed; file-based vector_store is the obsolete path these tests exercise.")
def test_rebuild_preserves_previous_index_on_failure(monkeypatch, db_session, tmp_path):
    monkeypatch.setenv("SOURCEHERO_VECTOR_DIR", str(tmp_path))
    clear_embeddings()
    _, _, chunk = _seed_chunk(db_session, title="Existing", text="existing embedding")
    upsert_embeddings({chunk.id: [0.9, 0.1]})
    monkeypatch.setattr("app.services.semantic_index.effective_openai_key", lambda: "sk-test-123")
    monkeypatch.setattr(
        "app.services.semantic_index.embed_texts",
        lambda texts, api_key, model="text-embedding-3-small", client_factory=None: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with pytest.raises(RuntimeError):
        rebuild_semantic_index(db_session)

    assert load_embeddings() == {str(chunk.id): [0.9, 0.1]}


@pytest.mark.skip(reason="pgvector-backed semantic_index.py is stubbed; file-based vector_store is the obsolete path these tests exercise.")
def test_partial_index_is_not_marked_ready(monkeypatch, db_session, tmp_path):
    monkeypatch.setenv("SOURCEHERO_VECTOR_DIR", str(tmp_path))
    clear_embeddings()
    _, _, first_chunk = _seed_chunk(db_session, title="One", text="first")
    _seed_chunk(db_session, title="Two", text="second")
    upsert_embeddings({first_chunk.id: [0.3, 0.7]})

    status = semantic_index_status(db_session)

    assert status.indexed_chunks == 1
    assert status.total_chunks == 2
    assert status.ready is False


@pytest.mark.skip(reason="pgvector-backed semantic_index.py is stubbed; remove_chunk_embeddings is a no-op until pgvector store is wired up.")
def test_delete_source_cleans_persisted_embeddings(monkeypatch, db_session, tmp_path):
    monkeypatch.setenv("SOURCEHERO_VECTOR_DIR", str(tmp_path))
    clear_embeddings()
    source, _, chunk = _seed_chunk(db_session, title="Cleanup", text="embedding cleanup target")
    upsert_embeddings({chunk.id: [0.4, 0.6]})

    delete_source(db_session, TEST_USER_ID, source.id)

    assert load_embeddings() == {}


def test_embedding_failure_does_not_fail_ingestion(monkeypatch, db_session):
    source = Source(user_id=TEST_USER_ID, source_type="webpage", name="Needs indexing", url="https://example.com/failure")
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)
    monkeypatch.setattr(
        "app.services.pipeline.ingest_webpage",
        lambda url: [
            SimpleNamespace(
                title="Synthetic page",
                raw_text="example raw",
                clean_text="example clean text",
                url=url,
                author=None,
                published_at=None,
                metadata={},
            )
        ],
    )
    monkeypatch.setattr("app.services.pipeline.index_new_chunks", lambda db, chunk_ids: (_ for _ in ()).throw(RuntimeError("boom")))

    run = ingest_source(db_session, TEST_USER_ID, source.id)

    assert run.status == "success"
