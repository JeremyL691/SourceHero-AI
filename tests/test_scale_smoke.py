"""Scale smoke test for DOM-22.

Verifies that claim cluster assignment has a fixed upper bound of SQL queries
per claim (independent of total claim/cluster scale) and operates efficiently
using pgvector index lookups instead of in-memory Python O(N) scans.
"""

from __future__ import annotations

import time
import uuid

import numpy as np
import pytest
from sqlalchemy import event, insert

from app.claims.cluster import assign_cluster
from app.core import time as clock
from app.models.claim import EMBEDDING_DIM, Claim, ClaimCluster
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.user import User


@pytest.mark.slow
@pytest.mark.db
def test_scale_smoke_cluster_assignment_fixed_query_bound(db, engine):
    user = User(email=f"scale_{uuid.uuid4().hex[:8]}@example.com", password_hash="pw")
    db.add(user)
    db.flush()

    source = Source(user_id=user.id, source_type="note", name="Scale Test Source")
    db.add(source)
    db.flush()

    doc = Document(
        user_id=user.id,
        source_id=source.id,
        identity_key=f"scale_doc:{uuid.uuid4()}",
        title="Scale Doc",
    )
    db.add(doc)
    db.flush()

    now = clock.utcnow()
    version = DocumentVersion(
        document_id=doc.id,
        user_id=user.id,
        version_no=1,
        clean_text="Synthetic text for 5000 claims.",
        content_hash="hash_scale_5000",
        effective_at=now,
    )
    db.add(version)
    db.flush()

    # Pre-seed 20 clusters with centroids
    rng = np.random.default_rng(42)
    clusters = []
    for i in range(20):
        vec = rng.normal(size=EMBEDDING_DIM)
        vec /= np.linalg.norm(vec)
        c = ClaimCluster(
            user_id=user.id,
            label=f"cluster_{i}",
            centroid=vec.tolist(),
            size=250,
            doc_count=1,
        )
        clusters.append(c)
    db.add_all(clusters)
    db.flush()

    # Bulk generate and insert 5000 synthetic claims
    claims_data = []
    for i in range(5000):
        cluster_idx = i % 20
        centroid = np.array(clusters[cluster_idx].centroid)
        noise = rng.normal(scale=0.05, size=EMBEDDING_DIM)
        vec = centroid + noise
        vec /= np.linalg.norm(vec)
        claims_data.append(
            {
                "user_id": user.id,
                "document_id": doc.id,
                "version_id": version.id,
                "chunk_id": None,
                "statement": f"Synthetic claim statement {i}",
                "quote": f"Synthetic claim quote {i}",
                "char_start": i * 10,
                "char_end": i * 10 + 9,
                "claim_hash": f"hash_{i}_{uuid.uuid4().hex[:12]}",
                "status": "active",
                "cluster_id": clusters[cluster_idx].id,
                "embedding": vec.tolist(),
                "asserted_at": now,
                "observed_at": now,
                "extractor": "synthetic",
                "confidence": 0.8,
            }
        )

    t0_insert = time.perf_counter()
    db.execute(insert(Claim), claims_data)
    db.commit()
    t_insert = time.perf_counter() - t0_insert
    assert t_insert < 5.0, f"5000 claims insert took {t_insert:.2f}s, expected < 5.0s"

    # Measure SQL query count per cluster assignment
    query_count = 0

    def query_listener(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", query_listener)

    try:
        # Test 10 new claims assignment against the 5000 existing claims and 20 clusters
        test_claims = []
        for j in range(10):
            vec = rng.normal(size=EMBEDDING_DIM)
            vec /= np.linalg.norm(vec)
            claim = Claim(
                user_id=user.id,
                document_id=doc.id,
                version_id=version.id,
                statement=f"New test claim {j}",
                quote=f"New test quote {j}",
                char_start=60000 + j * 10,
                char_end=60000 + j * 10 + 9,
                claim_hash=f"hash_new_{j}_{uuid.uuid4().hex[:8]}",
                status="active",
                embedding=vec.tolist(),
                asserted_at=now,
                observed_at=now,
                extractor="synthetic",
                confidence=0.8,
            )
            db.add(claim)
            db.flush()
            test_claims.append(claim)

        query_counts_per_claim = []
        t0_assign = time.perf_counter()

        for claim in test_claims:
            count_before = query_count
            assign_cluster(db, claim)
            db.flush()
            count_after = query_count
            diff = count_after - count_before
            query_counts_per_claim.append(diff)

        t_assign_total = time.perf_counter() - t0_assign
        avg_assign_time = t_assign_total / len(test_claims)

        # Assert fixed upper bound: at most 5 queries per claim assignment
        for idx, cnt in enumerate(query_counts_per_claim):
            assert cnt <= 5, f"Claim {idx} used {cnt} queries (expected <= 5)"

        # Assert total assignment time is under 1.0s (measured typically < 50ms)
        assert t_assign_total < 1.0, f"Total assignment time {t_assign_total:.3f}s exceeded 1.0s"
        assert avg_assign_time < 0.2, f"Average assignment time {avg_assign_time:.3f}s exceeded 0.2s"
    finally:
        event.remove(engine, "before_cursor_execute", query_listener)
