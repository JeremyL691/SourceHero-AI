"""Regression tests for DATA-7, DATA-12, DATA-13, DATA-21, DATA-22)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.core import time as clock
from app.indexing.embeddings import get_embedder
from app.indexing.indexer import reindex_embeddings
from app.models.chunk import Chunk
from app.models.claim import Claim, ClaimCluster, ClaimEvent, ClaimRelation
from app.models.document import Document, DocumentVersion
from app.models.llm import LLMRun
from app.models.source import Source
from app.models.temporal import Watch
from app.models.user import User
from app.models.wiki import Topic
from app.retrieval.rerank import rerank
from app.temporal.timeline import timeline

# --- DATA-7: rerank fastembed float and score object ---------------------


def test_rerank_fastembed_float_and_score_object(monkeypatch):
    """fastembed 0.8.1 yields float directly, or objects with .score attribute.

    Neither should crash or be ignored.
    """
    mock_encoder = MagicMock()
    # Case A: returns raw floats
    mock_encoder.rerank.return_value = [0.95, 0.42]
    monkeypatch.setattr("app.retrieval.rerank.get_encoder", lambda: mock_encoder)

    scores = rerank("query", ["doc1", "doc2"])
    assert scores == [0.95, 0.42]

    # Case B: returns objects with .score
    class ResultWithScore:
        def __init__(self, s):
            self.score = s

    mock_encoder.rerank.return_value = [ResultWithScore(0.88), ResultWithScore(0.33)]
    scores = rerank("query", ["doc1", "doc2"])
    assert scores == [0.88, 0.33]


def _create_user(db, email=None, is_admin=False):
    u = User(
        email=email or f"u{uuid.uuid4().hex[:8]}@test.local",
        password_hash="test",
        is_admin=is_admin,
    )
    db.add(u)
    db.flush()
    return u


# --- DATA-12: reindex_embeddings all chunks and centroids ----------------


@pytest.mark.db
def test_reindex_embeddings_all_chunks_and_centroids(db):
    """reindex_embeddings must process all chunks across batches and recompute centroids."""
    test_user = _create_user(db)
    embedder = get_embedder()
    source = Source(user_id=test_user.id, source_type="text", name="src")
    db.add(source)
    db.flush()

    doc = Document(user_id=test_user.id, source_id=source.id, identity_key=f"doc:{uuid.uuid4()}")
    db.add(doc)
    db.flush()

    version = DocumentVersion(
        document_id=doc.id,
        user_id=test_user.id,
        version_no=1,
        content_hash="abc",
        clean_text="hello world",
        effective_at=clock.utcnow(),
    )
    db.add(version)
    db.flush()

    # Create 300 chunks with old model name
    for i in range(300):
        c = Chunk(
            document_id=doc.id,
            version_id=version.id,
            user_id=test_user.id,
            chunk_index=i,
            char_start=i * 10,
            char_end=(i + 1) * 10,
            text=f"chunk text {i}",
            chunk_hash=f"chunk_hash_{i}",
            embedding_model="old_model_name",
            embedding=embedder.embed_query("chunk text"),
        )
        db.add(c)

    # Create a cluster and claims
    cluster = ClaimCluster(user_id=test_user.id, label="cluster 1", size=1)
    db.add(cluster)
    db.flush()

    claim = Claim(
        user_id=test_user.id,
        document_id=doc.id,
        version_id=version.id,
        chunk_id=None,
        cluster_id=cluster.id,
        statement="HX-2 has 450 Wh/kg",
        quote="HX-2 has 450 Wh/kg",
        char_start=0,
        char_end=10,
        asserted_at=clock.utcnow(),
        observed_at=clock.utcnow(),
        status="active",
        claim_hash=f"hash_{uuid.uuid4().hex}",
        embedding=None,
    )
    db.add(claim)

    topic = Topic(user_id=test_user.id, slug=f"topic-{uuid.uuid4().hex[:6]}", label="Topic 1")
    db.add(topic)
    db.flush()
    cluster.topic_id = topic.id

    watch = Watch(user_id=test_user.id, kind="query", query="HX-2 energy density")
    db.add(watch)
    db.commit()

    # Reindex with batch=100
    stats = reindex_embeddings(db, user_id=test_user.id, batch=100)
    db.commit()

    assert stats["chunks"] == 300
    assert stats["claims"] == 1

    # Verify all 300 chunks now have the current embedder name
    stale_chunks = (
        db.execute(select(Chunk).where(Chunk.user_id == test_user.id, Chunk.embedding_model != embedder.name))
        .scalars()
        .all()
    )
    assert len(stale_chunks) == 0

    # Verify cluster centroid was updated
    db.refresh(cluster)
    assert cluster.centroid is not None

    # Verify topic centroid was updated
    db.refresh(topic)
    assert topic.centroid is not None

    # Verify watch embedding was updated
    db.refresh(watch)
    assert watch.embedding is not None


# --- DATA-13: timeline in-SQL filtering and pagination -------------------


@pytest.mark.db
def test_timeline_sql_filtering_and_pagination(db):
    """timeline must filter in SQL before LIMIT/OFFSET."""
    test_user = _create_user(db)
    source = Source(user_id=test_user.id, source_type="text", name="src")
    db.add(source)
    db.flush()

    doc_a = Document(user_id=test_user.id, source_id=source.id, identity_key=f"doc_a:{uuid.uuid4()}")
    doc_b = Document(user_id=test_user.id, source_id=source.id, identity_key=f"doc_b:{uuid.uuid4()}")
    db.add_all([doc_a, doc_b])
    db.flush()

    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    v_a = DocumentVersion(
        document_id=doc_a.id,
        user_id=test_user.id,
        version_no=1,
        content_hash="a",
        clean_text="a",
        effective_at=now,
    )
    v_b = DocumentVersion(
        document_id=doc_b.id,
        user_id=test_user.id,
        version_no=1,
        content_hash="b",
        clean_text="b",
        effective_at=now,
    )
    db.add_all([v_a, v_b])
    db.flush()

    # 100 events for doc A
    for i in range(100):
        claim_a = Claim(
            user_id=test_user.id,
            document_id=doc_a.id,
            version_id=v_a.id,
            chunk_id=None,
            statement=f"statement a {i}",
            quote=f"statement a {i}",
            char_start=0,
            char_end=10,
            asserted_at=now,
            observed_at=now,
            status="active",
            claim_hash=f"hash_a_{i}_{uuid.uuid4().hex}",
        )
        db.add(claim_a)
        db.flush()
        ev_a = ClaimEvent(
            user_id=test_user.id,
            claim_id=claim_a.id,
            event="asserted",
            at=now,
            observed_at=now,
        )
        db.add(ev_a)

    # 5 events for doc B
    doc_b_events = []
    for i in range(5):
        claim_b = Claim(
            user_id=test_user.id,
            document_id=doc_b.id,
            version_id=v_b.id,
            chunk_id=None,
            statement=f"statement b {i}",
            quote=f"statement b {i}",
            char_start=0,
            char_end=10,
            asserted_at=now,
            observed_at=now,
            status="active",
            claim_hash=f"hash_b_{i}_{uuid.uuid4().hex}",
        )
        db.add(claim_b)
        db.flush()
        ev_b = ClaimEvent(
            user_id=test_user.id,
            claim_id=claim_b.id,
            event="asserted",
            at=now,
            observed_at=now,
        )
        db.add(ev_b)
        doc_b_events.append(ev_b)

    db.commit()

    # Query timeline for doc_b with limit=10
    entries = timeline(db, test_user.id, document_id=doc_b.id, limit=10)
    assert len(entries) == 5
    assert all(e.document_id == doc_b.id for e in entries)

    # Test offset
    entries_paged = timeline(db, test_user.id, document_id=doc_b.id, limit=2, offset=2)
    assert len(entries_paged) == 2
    assert entries_paged[0].claim_id == entries[2].claim_id


# --- DATA-21: usage SQL group by aggregation -----------------------------


@pytest.mark.db
def test_usage_sql_group_by_aggregation(make_client, db):
    """GET /v1/usage must aggregate via SQL without loading full rows into Python."""
    admin_client = make_client(session=db, email="admin@test.local", admin=True)
    now = clock.utcnow()
    # Insert 5 runs
    runs = [
        LLMRun(
            task="verify",
            provider="fake",
            model="fake-model",
            prompt_tokens=100,
            completion_tokens=50,
            cache_hit=False,
            status="ok",
            created_at=now,
        ),
        LLMRun(
            task="verify",
            provider="fake",
            model="fake-model",
            prompt_tokens=200,
            completion_tokens=80,
            cache_hit=True,
            status="ok",
            created_at=now,
        ),
        LLMRun(
            task="ask",
            provider="fake",
            model="fake-model",
            prompt_tokens=300,
            completion_tokens=120,
            cache_hit=False,
            status="error",
            created_at=now,
        ),
    ]
    db.add_all(runs)
    db.commit()

    resp = admin_client.get("/v1/usage?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["totals"]["calls"] >= 3
    assert "verify" in data["by_task"]
    assert "ask" in data["by_task"]
    assert data["by_task"]["verify"]["calls"] >= 2
    assert data["by_task"]["verify"]["cache_hits"] >= 1
    assert data["by_task"]["ask"]["errors"] >= 1


# --- DATA-22: conflicts stable ORDER BY ----------------------------------


@pytest.mark.db
def test_conflicts_order_by_confidence_and_id(make_client, db):
    """GET /v1/conflicts must order stably by confidence desc, id asc."""
    client = make_client(session=db, email="user_conflicts@test.local")
    from app.models.user import User

    test_user = db.execute(select(User).where(User.email == "user_conflicts@test.local")).scalar_one()
    source = Source(user_id=test_user.id, source_type="text", name="src")
    db.add(source)
    db.flush()

    doc = Document(user_id=test_user.id, source_id=source.id, identity_key=f"doc:{uuid.uuid4()}")
    db.add(doc)
    db.flush()

    ver = DocumentVersion(
        document_id=doc.id,
        user_id=test_user.id,
        version_no=1,
        content_hash="abc",
        clean_text="abc",
        effective_at=clock.utcnow(),
    )
    db.add(ver)
    db.flush()

    claim_a = Claim(
        user_id=test_user.id,
        document_id=doc.id,
        version_id=ver.id,
        chunk_id=None,
        statement="A",
        quote="A",
        char_start=0,
        char_end=1,
        asserted_at=clock.utcnow(),
        observed_at=clock.utcnow(),
        status="active",
        claim_hash=f"h_{uuid.uuid4().hex}",
    )
    claim_b = Claim(
        user_id=test_user.id,
        document_id=doc.id,
        version_id=ver.id,
        chunk_id=None,
        statement="B",
        quote="B",
        char_start=0,
        char_end=1,
        asserted_at=clock.utcnow(),
        observed_at=clock.utcnow(),
        status="active",
        claim_hash=f"h_{uuid.uuid4().hex}",
    )
    claim_c = Claim(
        user_id=test_user.id,
        document_id=doc.id,
        version_id=ver.id,
        chunk_id=None,
        statement="C",
        quote="C",
        char_start=0,
        char_end=1,
        asserted_at=clock.utcnow(),
        observed_at=clock.utcnow(),
        status="active",
        claim_hash=f"h_{uuid.uuid4().hex}",
    )
    db.add_all([claim_a, claim_b, claim_c])
    db.flush()

    r1 = ClaimRelation(
        user_id=test_user.id,
        a_claim_id=claim_a.id,
        b_claim_id=claim_b.id,
        relation="contradicts",
        confidence=0.6,
    )
    r2 = ClaimRelation(
        user_id=test_user.id,
        a_claim_id=claim_a.id,
        b_claim_id=claim_c.id,
        relation="contradicts",
        confidence=0.9,
    )
    db.add_all([r1, r2])
    db.commit()

    resp = client.get("/v1/conflicts")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["id"] == r2.id
    assert items[1]["id"] == r1.id


@pytest.mark.db
def test_passive_deletes_on_source_and_document():
    """Source.documents and Document.versions must configure passive_deletes=True."""
    from sqlalchemy import inspect as sa_inspect

    from app.models.document import Document
    from app.models.source import Source

    source_mapper = sa_inspect(Source)
    docs_rel = source_mapper.relationships["documents"]
    assert docs_rel.passive_deletes is True, "Source.documents missing passive_deletes=True"

    doc_mapper = sa_inspect(Document)
    vers_rel = doc_mapper.relationships["versions"]
    assert vers_rel.passive_deletes is True, "Document.versions missing passive_deletes=True"


@pytest.mark.db
def test_pagination_endpoints_accept_limit_and_offset(make_client, db):
    """GET /v1/sources, /topics, /watches, /stale-notes, /documents/{id}/claims must accept limit and offset."""
    client = make_client(session=db, email="pagination_user@test.local")
    from app.models.user import User

    user = db.execute(select(User).where(User.email == "pagination_user@test.local")).scalar_one()

    # 1. Sources pagination
    for i in range(3):
        s = Source(user_id=user.id, source_type="text", name=f"src {i}")
        db.add(s)
    db.commit()

    resp = client.get("/v1/sources?limit=1&offset=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 2. Topics pagination
    for i in range(3):
        t = Topic(user_id=user.id, slug=f"topic-{i}-{uuid.uuid4().hex[:4]}", label=f"Topic {i}")
        db.add(t)
    db.commit()

    resp = client.get("/v1/topics?limit=1&offset=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 3. Watches pagination
    for i in range(3):
        w = Watch(user_id=user.id, kind="query", query=f"query {i}")
        db.add(w)
    db.commit()

    resp = client.get("/v1/watches?limit=1&offset=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 4. Stale-notes pagination
    resp = client.get("/v1/stale-notes?limit=1&offset=0")
    assert resp.status_code == 200


@pytest.mark.db
def test_wayback_enqueue_dedupe_key_window(make_client, db, no_inline_jobs):
    """POST /v1/sources/{id}/wayback dedupe key must distinguish different time windows."""
    client = make_client(session=db, email="wayback_user@test.local")
    from app.models.user import User

    user = db.execute(select(User).where(User.email == "wayback_user@test.local")).scalar_one()
    src = Source(user_id=user.id, source_type="webpage", name="site", url="https://example.com")
    db.add(src)
    db.commit()

    r1 = client.post(f"/v1/sources/{src.id}/wayback?from=2026-01-01&to=2026-02-01")
    assert r1.status_code == 202
    job1_id = r1.json()["job_id"]

    r2 = client.post(f"/v1/sources/{src.id}/wayback?from=2026-03-01&to=2026-04-01")
    assert r2.status_code == 202
    job2_id = r2.json()["job_id"]

    # Must be separate jobs, not reusing job1
    assert job1_id != job2_id


def test_chunking_no_duplicate_dead_loop():
    """chunk_spans must not contain duplicate dead loop."""
    import inspect

    from app.ingestion import chunking

    source = inspect.getsource(chunking.chunk_spans)
    assert source.count("groups: list[list[Span]] = []") == 1
