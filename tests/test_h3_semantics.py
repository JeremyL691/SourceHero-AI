"""Regression tests for SourceHero v2.1 semantics.

DOM-1: Un-united numbers (counts, raw currencies) participate in conflict detection,
       and numbers_differ prevents false-positive supported/duplicate/carried.
DOM-9: Overlapping intervals do not conflict.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.claims.signals import numbers_conflict, numbers_differ
from app.verify.verifier import judge_sentence_against


def test_numbers_conflict_interval_overlap_no_conflict():
    """DOM-9: Overlapping intervals must not be judged as conflicting."""
    a = "Battery pack output from 380 to 450 Wh/kg."
    b = "Battery pack output is 450 Wh/kg."
    # 450 Wh/kg is inside [380, 450] Wh/kg -> no conflict
    assert numbers_conflict(a, b) is False
    assert numbers_differ(a, b) is False


def test_numbers_conflict_different_counts_conflict():
    """DOM-1: Statements with different counts of the same entity must conflict."""
    a = "The clinical trial enrolled 900 participants."
    b = "The clinical trial enrolled 612 participants."
    assert numbers_conflict(a, b) is True
    assert numbers_differ(a, b) is True

    verdict, confidence, reason = judge_sentence_against(a, b)
    assert verdict in ("contradicted", "disputed")
    assert verdict != "supported"


def test_numbers_differ_prevents_supported():
    """DOM-1: When numbers differ, verifier must never return supported."""
    a = "Revenue was $500 million in Q3."
    b = "Revenue was $300 million in Q3."
    assert numbers_conflict(a, b) is True
    assert numbers_differ(a, b) is True

    verdict, _, _ = judge_sentence_against(a, b)
    assert verdict != "supported"
    assert verdict in ("contradicted", "disputed")


def test_interval_vs_interval_overlap():
    """DOM-9: Two intervals overlapping must not conflict."""
    a = "Expected operating margin is 10–20%."
    b = "Operating margin is projected between 15% and 25%."
    # [10, 20] and [15, 25] overlap in [15, 20]
    assert numbers_conflict(a, b) is False
    assert numbers_differ(a, b) is False


def test_interval_vs_interval_disjoint_conflicts():
    """Two disjoint intervals must conflict."""
    a = "Expected operating margin is 10–20%."
    b = "Operating margin is projected between 25% and 30%."
    assert numbers_conflict(a, b) is True
    assert numbers_differ(a, b) is True


def test_relate_numbers_differ_yields_contradiction_not_duplicate():
    """DOM-1: Relate must not return duplicate or supports when numbers differ."""
    from datetime import datetime

    from app.claims.relate import heuristic_relation
    from app.models.claim import Claim

    c1 = Claim(
        id=1,
        user_id=None,
        document_id=None,
        version_id=1,
        statement="The trial enrolled 900 participants.",
        quote="The trial enrolled 900 participants.",
        char_start=0,
        char_end=35,
        asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    c2 = Claim(
        id=2,
        user_id=None,
        document_id=None,
        version_id=1,
        statement="The trial enrolled 612 participants.",
        quote="The trial enrolled 612 participants.",
        char_start=0,
        char_end=35,
        asserted_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    draft = heuristic_relation(c1, c2, similarity=0.92)
    assert draft is not None
    assert draft.relation == "contradicts"
    assert draft.relation not in ("duplicate", "supports")


def test_reconcile_numbers_differ_not_carried():
    """DOM-1: Reconcile must never carry a claim whose numbers differ."""
    from datetime import datetime

    from app.models.claim import Claim
    from app.temporal.reconcile import _best_match

    c1 = Claim(
        id=1,
        user_id=None,
        document_id=None,
        version_id=1,
        statement="The trial enrolled 900 participants.",
        quote="The trial enrolled 900 participants.",
        char_start=0,
        char_end=35,
        asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    c2 = Claim(
        id=2,
        user_id=None,
        document_id=None,
        version_id=2,
        statement="The trial enrolled 612 participants.",
        quote="The trial enrolled 612 participants.",
        char_start=0,
        char_end=35,
        asserted_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    # Cosine score simulation high enough to otherwise carry
    c1.embedding = [0.1] * 384
    c2.embedding = [0.1] * 384
    matched, outcome = _best_match(c1, [c2], carry_sim=0.7, pair_sim=0.5)
    assert outcome != "carried"
    assert outcome == "superseded"
    assert matched is not None and matched.id == 2


def test_negation_asymmetry_both_negated_is_false():
    """DOM-7: 'did not reduce' vs 'no reduction' must not be flagged as asymmetric."""
    from app.claims.signals import negation_asymmetry

    assert negation_asymmetry("did not reduce", "no reduction") is False


def test_negation_chinese_whitelist_not_negated():
    """DOM-7: '未来三年' and whitelist words must not be treated as negations."""
    from app.claims.signals import negation_asymmetry

    # Neither sentence is negated
    assert negation_asymmetry("未来三年产量翻番", "三年内产量翻番") is False
    # Only the second sentence is negated ("未翻番")
    assert negation_asymmetry("未来三年产量翻番", "产量未翻番") is True


def test_date_precision_containment():
    """DOM-10: 2026 contains September 2026; Q3 contains September 2026."""
    from app.claims.signals import date_conflict

    # 2026 ⊃ September 2026 -> no conflict
    assert (
        date_conflict("Mass production begins in 2026.", "Mass production begins in September 2026.") is False
    )
    # Q3 2026 ⊃ September 2026 -> no conflict
    assert (
        date_conflict("Mass production begins in Q3 2026.", "Mass production begins in September 2026.")
        is False
    )
    # September 2026 vs January 2027 -> conflict
    assert (
        date_conflict("Mass production begins in September 2026.", "Mass production begins in January 2027.")
        is True
    )


def test_date_conflict_not_masked_by_shared_announcement_date():
    """DOM-10: Shared irrelevant announcement dates must not mask conflicting event dates."""
    from app.claims.signals import date_conflict

    a = "Announced in March 2026, mass production begins in September 2026."
    b = "Announced in March 2026, mass production begins in January 2027."
    assert date_conflict(a, b) is True


def test_quantities_excluded_from_date_parsing():
    """DOM-10: $2000 and 2048 cores must not be parsed as years."""
    from app.claims.numbers import extract_dates

    assert extract_dates("The consulting fee was $2000 in total.", include_bare_year=True) == []
    assert extract_dates("The GPU cluster deployed 2048 cores.", include_bare_year=True) == []


def test_event_word_boundary_matching():
    """DOM-10: 'relationship' must not trigger event word 'ship'."""
    from app.claims.numbers import extract_dates

    # 'relationship' has no event word, so bare year 2026 should not be extracted
    assert extract_dates("The relationship between partners started in 2026.") != []
    assert extract_dates("The relationship between partners in 2026.") == []


def test_temporal_update_cues():
    """DOM-10: Bare 'expected' removed, new cues added."""
    from app.claims.signals import temporal_update_cue

    assert temporal_update_cue("is expected to deliver 380 Wh/kg") is False
    assert temporal_update_cue("is now expected to deliver 380 Wh/kg") is True
    assert temporal_update_cue("delivery has been pushed back to 2027") is True
    assert temporal_update_cue("delayed to January 2027") is True
    assert temporal_update_cue("量产已推迟至2027年") is True
    assert temporal_update_cue("预期指标下调10%") is True


def test_none_asserted_at_cannot_supersede():
    """DOM-10: Missing asserted_at must produce conflict, never supersedes."""
    from datetime import datetime

    from app.claims.relate import heuristic_relation
    from app.models.claim import Claim

    c1 = Claim(
        id=1,
        user_id=None,
        document_id=None,
        version_id=1,
        statement="Mass production begins in September 2026.",
        quote="Mass production begins in September 2026.",
        char_start=0,
        char_end=42,
        asserted_at=None,
    )
    c2 = Claim(
        id=2,
        user_id=None,
        document_id=None,
        version_id=2,
        statement="Mass production now expected to begin in January 2027.",
        quote="Mass production now expected to begin in January 2027.",
        char_start=0,
        char_end=54,
        asserted_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    draft = heuristic_relation(c1, c2, similarity=0.85)
    assert draft is not None
    assert draft.relation != "supersedes"
    assert draft.relation == "contradicts"


@pytest.mark.db
def test_relation_direction_canonicalization(db, user_factory):
    """DOM-3: When a.id > b.id, store_relation flips direction so (a_claim_id, b_claim_id) orientation is preserved."""
    from app.claims.relate import RelationDraft, store_relation
    from app.models.claim import Claim
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    # Create dummy source and document
    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()
    doc = Document(
        user_id=user_id, source_id=src.id, title="Test Doc", identity_key=f"doc:{uuid.uuid4().hex}"
    )
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        user_id=user_id,
        document_id=doc.id,
        version_no=1,
        content_hash="h1",
        clean_text="hello",
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(ver)
    db.flush()

    # Create two claims where claim2 has larger id than claim1
    c1 = Claim(
        user_id=user_id,
        document_id=doc.id,
        version_id=ver.id,
        statement="Older claim.",
        quote="Older claim.",
        char_start=0,
        char_end=12,
        asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        claim_hash="hash1",
    )
    db.add(c1)
    db.flush()
    c2 = Claim(
        user_id=user_id,
        document_id=doc.id,
        version_id=ver.id,
        statement="Newer claim superseding older.",
        quote="Newer claim superseding older.",
        char_start=0,
        char_end=30,
        asserted_at=datetime(2026, 2, 1, tzinfo=UTC),
        observed_at=datetime(2026, 2, 1, tzinfo=UTC),
        claim_hash="hash2",
    )
    db.add(c2)
    db.flush()

    assert c2.id > c1.id

    # c2 supersedes c1: when passed as (c2, c1), draft.direction is "a_to_b" (c2 -> c1)
    draft = RelationDraft(
        relation="supersedes",
        direction="a_to_b",
        confidence=0.8,
        signals={},
        rationale="c2 updates c1",
    )
    rel = store_relation(db, user_id, c2, c1, draft, method="heuristic")
    assert rel is not None
    assert rel.a_claim_id == c1.id
    assert rel.b_claim_id == c2.id
    # Since in stored relation a is c1 and b is c2, c2 -> c1 must be "b_to_a"
    assert rel.direction == "b_to_a"


@pytest.mark.db
def test_candidate_pairs_cross_document_knn(db, user_factory):
    """DOM-12: A document with many claims still finds cross-document candidates."""
    from app.claims.relate import candidate_pairs
    from app.indexing.embeddings import get_embedder
    from app.models.claim import Claim
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    embedder = get_embedder()
    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    # Document 1: 15 claims
    doc1 = Document(user_id=user_id, source_id=src.id, title="Doc 1", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(doc1)
    db.flush()
    ver1 = DocumentVersion(
        user_id=user_id,
        document_id=doc1.id,
        version_no=1,
        content_hash="h1",
        clean_text="doc1",
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(ver1)
    db.flush()

    doc1_claims = []
    for i in range(15):
        c = Claim(
            user_id=user_id,
            document_id=doc1.id,
            version_id=ver1.id,
            statement=f"Energy density of battery test {i} is 450 Wh/kg.",
            quote=f"Energy density of battery test {i} is 450 Wh/kg.",
            char_start=0,
            char_end=40,
            asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            claim_hash=f"hash_doc1_{i}",
            status="active",
        )
        c.embedding = embedder.embed_query(c.statement)
        db.add(c)
        doc1_claims.append(c)

    # Document 2: 1 claim with similar topic
    doc2 = Document(user_id=user_id, source_id=src.id, title="Doc 2", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(doc2)
    db.flush()
    ver2 = DocumentVersion(
        user_id=user_id,
        document_id=doc2.id,
        version_no=1,
        content_hash="h2",
        clean_text="doc2",
        effective_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    db.add(ver2)
    db.flush()

    c_cross = Claim(
        user_id=user_id,
        document_id=doc2.id,
        version_id=ver2.id,
        statement="Energy density of battery test is 380 Wh/kg.",
        quote="Energy density of battery test is 380 Wh/kg.",
        char_start=0,
        char_end=40,
        asserted_at=datetime(2026, 1, 2, tzinfo=UTC),
        observed_at=datetime(2026, 1, 2, tzinfo=UTC),
        claim_hash="hash_doc2_0",
        status="active",
    )
    c_cross.embedding = embedder.embed_query(c_cross.statement)
    db.add(c_cross)
    db.flush()

    # candidate_pairs for doc1_claims[0] must find c_cross despite 14 closer same-doc claims!
    candidates = candidate_pairs(db, doc1_claims[0], limit=5)
    assert len(candidates) > 0
    candidate_claim_ids = [c.id for c, _ in candidates]
    assert c_cross.id in candidate_claim_ids
    # None of doc1's claims should be in candidate pairs
    for d1c in doc1_claims:
        assert d1c.id not in candidate_claim_ids


@pytest.mark.db
def test_cluster_merge_threshold_and_status_preservation(db, user_factory):
    """DOM-13, SEC-15: Heuristic 0.6 does not merge clusters; merge preserves all claim statuses."""
    from app.claims.relate import _merge_across_clusters
    from app.models.claim import Claim, ClaimCluster, ClaimRelation
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()
    doc = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        user_id=user_id,
        document_id=doc.id,
        version_no=1,
        content_hash="h",
        clean_text="doc",
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(ver)
    db.flush()

    # Two distinct clusters
    cluster1 = ClaimCluster(user_id=user_id, label="Topic 1", size=1, doc_count=1, centroid=[0.1] * 384)
    cluster2 = ClaimCluster(user_id=user_id, label="Topic 2", size=2, doc_count=1, centroid=[0.2] * 384)
    db.add_all([cluster1, cluster2])
    db.flush()

    c1 = Claim(
        user_id=user_id,
        document_id=doc.id,
        version_id=ver.id,
        statement="Claim in cluster 1",
        quote="Claim in cluster 1",
        char_start=0,
        char_end=18,
        asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        claim_hash="h_c1",
        cluster_id=cluster1.id,
        status="active",
        embedding=[0.1] * 384,
    )
    c2 = Claim(
        user_id=user_id,
        document_id=doc.id,
        version_id=ver.id,
        statement="Claim in cluster 2 active",
        quote="Claim in cluster 2 active",
        char_start=0,
        char_end=25,
        asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        claim_hash="h_c2",
        cluster_id=cluster2.id,
        status="active",
        embedding=[0.2] * 384,
    )
    c3 = Claim(
        user_id=user_id,
        document_id=doc.id,
        version_id=ver.id,
        statement="Claim in cluster 2 superseded",
        quote="Claim in cluster 2 superseded",
        char_start=0,
        char_end=29,
        asserted_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        claim_hash="h_c3",
        cluster_id=cluster2.id,
        status="superseded",
        embedding=[0.2] * 384,
    )
    db.add_all([c1, c2, c3])
    db.flush()

    # 1. Relation with confidence 0.6 (heuristic contradiction) -> must NOT merge
    rel_weak = ClaimRelation(
        user_id=user_id,
        a_claim_id=min(c1.id, c2.id),
        b_claim_id=max(c1.id, c2.id),
        relation="contradicts",
        confidence=0.6,
        method="heuristic",
    )
    db.add(rel_weak)
    db.flush()

    stats = {}
    _merge_across_clusters(db, user_id, [rel_weak], stats)
    assert stats.get("clusters_merged", 0) == 0
    assert db.get(ClaimCluster, cluster1.id) is not None
    assert db.get(ClaimCluster, cluster2.id) is not None

    # 2. Relation with confidence 0.8 (high confidence) -> DOES merge
    rel_strong = ClaimRelation(
        user_id=user_id,
        a_claim_id=min(c1.id, c2.id),
        b_claim_id=max(c1.id, c2.id),
        relation="contradicts",
        confidence=0.8,
        method="heuristic",
    )
    _merge_across_clusters(db, user_id, [rel_strong], stats)
    assert stats.get("clusters_merged", 0) == 1

    # Check that c3 (superseded) also got moved to the kept cluster!
    db.refresh(c1)
    db.refresh(c2)
    db.refresh(c3)
    assert c2.cluster_id == cluster1.id
    assert c3.cluster_id == cluster1.id
    # cluster2 must be deleted
    assert db.get(ClaimCluster, cluster2.id) is None


def test_camps_two_unrelated_controversies_two_components():
    """DOM-11: Two separate disputes output as two components, not fused into global A/B."""
    from app.claims.camps import compute_camps
    from app.models.claim import Claim, ClaimRelation

    def _c(ident: int) -> Claim:
        c = Claim()
        c.id = ident
        c.statement = f"Claim {ident}"
        c.document_id = uuid.uuid4()
        c.asserted_at = datetime(2026, 1, 1, tzinfo=UTC)
        c.status = "active"
        return c

    def _r(a: int, b: int, kind: str, label: str | None = None) -> ClaimRelation:
        r = ClaimRelation()
        r.a_claim_id, r.b_claim_id = sorted((a, b))
        r.relation = kind
        r.resolved_at = None
        r.user_label = label
        return r

    claims = [_c(1), _c(2), _c(3), _c(4)]
    relations = [_r(1, 2, "contradicts"), _r(3, 4, "contradicts")]

    camps = compute_camps(claims, relations)
    assert len(camps.components) == 2
    comp1 = camps.components[0]
    comp2 = camps.components[1]
    assert comp1.balanced is True
    assert comp2.balanced is True

    # Dispute 1 involves 1 and 2
    assert set(comp1.camps.get("A", []) + comp1.camps.get("B", [])) == {1, 2}
    # Dispute 2 involves 3 and 4
    assert set(comp2.camps.get("A", []) + comp2.camps.get("B", [])) == {3, 4}


def test_camps_confirmed_contradiction_remains_opposing():
    """DOM-11: A user-confirmed contradiction remains an opposing edge."""
    from app.claims.camps import compute_camps
    from app.models.claim import Claim, ClaimRelation

    c1 = Claim(id=1, statement="Statement 1", document_id=uuid.uuid4(), status="active")
    c2 = Claim(id=2, statement="Statement 2", document_id=uuid.uuid4(), status="active")
    rel = ClaimRelation(a_claim_id=1, b_claim_id=2, relation="contradicts", user_label="confirmed")

    camps = compute_camps([c1, c2], [rel])
    assert len(camps.components) == 1
    assert camps.components[0].balanced is True
    assert camps.components[0].camps.get("A") == [1]
    assert camps.components[0].camps.get("B") == [2]


def test_camps_frustrated_edges_deduplication():
    """DOM-11: Frustrated edges in an impossible triangle are deduplicated."""
    from app.claims.camps import compute_camps
    from app.models.claim import Claim, ClaimRelation

    c1 = Claim(id=1, statement="A", document_id=uuid.uuid4(), status="active")
    c2 = Claim(id=2, statement="B", document_id=uuid.uuid4(), status="active")
    c3 = Claim(id=3, statement="C", document_id=uuid.uuid4(), status="active")
    rels = [
        ClaimRelation(a_claim_id=1, b_claim_id=2, relation="contradicts"),
        ClaimRelation(a_claim_id=2, b_claim_id=3, relation="contradicts"),
        ClaimRelation(a_claim_id=1, b_claim_id=3, relation="contradicts"),
    ]

    camps = compute_camps([c1, c2, c3], rels)
    assert camps.balanced is False
    assert len(camps.frustrated_edges) == 1  # exactly 1 frustrated edge, not duplicated!
    assert camps.frustrated_edges == list(set(camps.frustrated_edges))


@pytest.mark.db
def test_reconcile_preserves_externally_superseded(db, user_factory):
    """DOM-2: Reconciling document X must not un-supersede claims superseded by document Y."""
    from app.models.claim import Claim
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d1 = Document(user_id=user_id, source_id=src.id, title="Doc 1", identity_key=f"doc:{uuid.uuid4().hex}")
    d2 = Document(user_id=user_id, source_id=src.id, title="Doc 2", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add_all([d1, d2])
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    v1 = DocumentVersion(
        user_id=user_id, document_id=d1.id, version_no=1, content_hash="h1", clean_text="v1", effective_at=t1
    )
    v2 = DocumentVersion(
        user_id=user_id, document_id=d2.id, version_no=1, content_hash="h2", clean_text="v2", effective_at=t2
    )
    db.add_all([v1, v2])
    db.flush()

    c2 = Claim(
        user_id=user_id,
        document_id=d2.id,
        version_id=v2.id,
        statement="New statement in doc 2",
        quote="New statement in doc 2",
        char_start=0,
        char_end=22,
        asserted_at=t2,
        observed_at=t2,
        claim_hash="h_c2",
        status="active",
    )
    db.add(c2)
    db.flush()

    c1 = Claim(
        user_id=user_id,
        document_id=d1.id,
        version_id=v1.id,
        statement="Old statement in doc 1",
        quote="Old statement in doc 1",
        char_start=0,
        char_end=22,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_c1",
        status="superseded",
        superseded_by=c2.id,
        valid_to=t2,
    )
    db.add(c1)
    db.flush()

    # Reconciling d1 must preserve c1's superseded status and valid_to
    reconcile_document(db, d1.id)
    db.flush()

    db.refresh(c1)
    assert c1.status == "superseded"
    assert c1.superseded_by == c2.id
    assert c1.valid_to == t2


@pytest.mark.db
def test_reconcile_does_not_reopen_rejected_relation(db, user_factory):
    """DOM-2, DOM-17: Reconciling must not reopen a relation with user_label='rejected'."""
    from app.models.claim import Claim, ClaimRelation
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d1 = Document(user_id=user_id, source_id=src.id, title="Doc 1", identity_key=f"doc:{uuid.uuid4().hex}")
    d2 = Document(user_id=user_id, source_id=src.id, title="Doc 2", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add_all([d1, d2])
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    v1 = DocumentVersion(
        user_id=user_id, document_id=d1.id, version_no=1, content_hash="h1", clean_text="v1", effective_at=t1
    )
    v2 = DocumentVersion(
        user_id=user_id, document_id=d2.id, version_no=1, content_hash="h2", clean_text="v2", effective_at=t2
    )
    db.add_all([v1, v2])
    db.flush()

    c1 = Claim(
        user_id=user_id,
        document_id=d1.id,
        version_id=v1.id,
        statement="Statement 1",
        quote="Statement 1",
        char_start=0,
        char_end=11,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_c1",
        status="active",
    )
    c2 = Claim(
        user_id=user_id,
        document_id=d2.id,
        version_id=v2.id,
        statement="Statement 2",
        quote="Statement 2",
        char_start=0,
        char_end=11,
        asserted_at=t2,
        observed_at=t2,
        claim_hash="h_c2",
        status="active",
    )
    db.add_all([c1, c2])
    db.flush()

    resolved_time = datetime(2026, 1, 3, tzinfo=UTC)
    rel = ClaimRelation(
        user_id=user_id,
        a_claim_id=min(c1.id, c2.id),
        b_claim_id=max(c1.id, c2.id),
        relation="contradicts",
        user_label="rejected",
        resolved_at=resolved_time,
    )
    db.add(rel)
    db.flush()

    reconcile_document(db, d1.id)
    db.flush()

    db.refresh(rel)
    # The rejected relation must NOT be reopened!
    assert rel.resolved_at is not None
    assert rel.user_label == "rejected"


@pytest.mark.db
def test_reconcile_idempotent_events_across_3_runs(db, user_factory):
    """DOM-17: 3 consecutive reconcile runs produce strictly identical event sets and observed_at."""
    from sqlalchemy import select

    from app.models.claim import Claim, ClaimEvent
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    v1 = DocumentVersion(
        user_id=user_id, document_id=d.id, version_no=1, content_hash="h1", clean_text="v1", effective_at=t1
    )
    v2 = DocumentVersion(
        user_id=user_id, document_id=d.id, version_no=2, content_hash="h2", clean_text="v2", effective_at=t2
    )
    db.add_all([v1, v2])
    db.flush()

    # V1: 18% traffic, and an extra sentence that gets retracted
    c1 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v1.id,
        statement="Downtown traffic fell by 18%.",
        quote="Downtown traffic fell by 18%.",
        char_start=0,
        char_end=28,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_c1",
        status="active",
        embedding=[0.1] * 384,
    )
    c_extra = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v1.id,
        statement="Old announcement that will be removed.",
        quote="Old announcement that will be removed.",
        char_start=30,
        char_end=68,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_extra",
        status="active",
        embedding=[-0.1] * 384,
    )
    # V2: 11% traffic
    c2 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v2.id,
        statement="Downtown traffic fell by 11%.",
        quote="Downtown traffic fell by 11%.",
        char_start=0,
        char_end=28,
        asserted_at=t2,
        observed_at=t2,
        claim_hash="h_c2",
        status="active",
        embedding=[0.1] * 384,
    )
    db.add_all([c1, c_extra, c2])
    db.flush()

    # Run 1
    reconcile_document(db, d.id)
    db.flush()

    def get_events():
        return [
            (e.id, e.event, e.claim_id, e.related_claim_id, e.at, e.observed_at)
            for e in db.execute(
                select(ClaimEvent)
                .join(Claim, Claim.id == ClaimEvent.claim_id)
                .where(Claim.document_id == d.id)
                .order_by(ClaimEvent.id)
            ).scalars()
        ]

    events_1 = get_events()
    assert len(events_1) == 2  # c1 superseded, c_extra retracted

    # Run 2
    reconcile_document(db, d.id)
    db.flush()
    events_2 = get_events()
    assert events_2 == events_1, "Events and observed_at must remain identical on 2nd run"

    # Run 3
    reconcile_document(db, d.id)
    db.flush()
    events_3 = get_events()
    assert events_3 == events_1, "Events and observed_at must remain identical on 3rd run"


@pytest.mark.db
def test_reconcile_suspect_version_skips_retraction(db, user_factory):
    """DOM-18: Soft-404 / suspect version marks version suspect and does NOT retract claims."""
    from app.models.claim import Claim
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    # Long text for v1 (500 chars)
    v1_text = "Detailed report with many claims. " * 15
    v1 = DocumentVersion(
        user_id=user_id,
        document_id=d.id,
        version_no=1,
        content_hash="h1",
        clean_text=v1_text,
        effective_at=t1,
    )
    # Suspect text for v2 (soft 404: 14 chars < 30% of 500 chars)
    v2_text = "404 Not Found"
    v2 = DocumentVersion(
        user_id=user_id,
        document_id=d.id,
        version_no=2,
        content_hash="h2",
        clean_text=v2_text,
        effective_at=t2,
    )
    db.add_all([v1, v2])
    db.flush()

    c1 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v1.id,
        statement="A detailed statement in version 1.",
        quote="A detailed statement in version 1.",
        char_start=0,
        char_end=33,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_c1",
        status="active",
    )
    db.add(c1)
    db.flush()

    reconcile_document(db, d.id)
    db.flush()

    db.refresh(c1)
    db.refresh(v2)
    # Must NOT be retracted
    assert c1.status == "active"
    assert v2.diff_stats is not None and v2.diff_stats.get("suspect") is True


@pytest.mark.db
def test_reconcile_conflict_resolved_uses_world_time(db, user_factory):
    """DOM-17: conflict_resolved event uses world time (asserted_at of resolving claim)."""
    from sqlalchemy import select

    from app.models.claim import Claim, ClaimEvent, ClaimRelation
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d1 = Document(user_id=user_id, source_id=src.id, title="Doc 1", identity_key=f"doc:{uuid.uuid4().hex}")
    d2 = Document(user_id=user_id, source_id=src.id, title="Doc 2", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add_all([d1, d2])
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 3, 1, tzinfo=UTC)
    v1 = DocumentVersion(
        user_id=user_id, document_id=d1.id, version_no=1, content_hash="h1", clean_text="v1", effective_at=t1
    )
    v2 = DocumentVersion(
        user_id=user_id, document_id=d1.id, version_no=2, content_hash="h2", clean_text="v2", effective_at=t2
    )
    v_other = DocumentVersion(
        user_id=user_id, document_id=d2.id, version_no=1, content_hash="ho", clean_text="vo", effective_at=t1
    )
    db.add_all([v1, v2, v_other])
    db.flush()

    c1 = Claim(
        user_id=user_id,
        document_id=d1.id,
        version_id=v1.id,
        statement="Downtown traffic fell by 18%.",
        quote="Downtown traffic fell by 18%.",
        char_start=0,
        char_end=28,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_c1",
        status="active",
        embedding=[0.1] * 384,
    )
    c_other = Claim(
        user_id=user_id,
        document_id=d2.id,
        version_id=v_other.id,
        statement="Downtown traffic was unchanged.",
        quote="Downtown traffic was unchanged.",
        char_start=0,
        char_end=31,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_co",
        status="active",
        embedding=[0.1] * 384,
    )
    c2 = Claim(
        user_id=user_id,
        document_id=d1.id,
        version_id=v2.id,
        statement="Downtown traffic fell by 11%.",
        quote="Downtown traffic fell by 11%.",
        char_start=0,
        char_end=28,
        asserted_at=t2,
        observed_at=t2,
        claim_hash="h_c2",
        status="active",
        embedding=[0.1] * 384,
    )
    db.add_all([c1, c_other, c2])
    db.flush()

    rel = ClaimRelation(
        user_id=user_id,
        a_claim_id=min(c1.id, c_other.id),
        b_claim_id=max(c1.id, c_other.id),
        relation="contradicts",
        confidence=0.9,
    )
    db.add(rel)
    db.flush()

    reconcile_document(db, d1.id)
    db.flush()

    event = db.execute(select(ClaimEvent).where(ClaimEvent.event == "conflict_resolved")).scalar_one_or_none()
    assert event is not None
    # Event 'at' must be t2 (the asserted_at of c2, or effective_at of v2), NOT now()!
    assert event.at == t2


@pytest.mark.db
def test_reconcile_global_matching_avoids_greedy_steal(db, user_factory):
    """DOM-18: Earlier claim does not greedily steal a successor that is identical to a later claim."""
    from app.models.claim import Claim
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    v1 = DocumentVersion(
        user_id=user_id, document_id=d.id, version_no=1, content_hash="h1", clean_text="v1", effective_at=t1
    )
    v2 = DocumentVersion(
        user_id=user_id, document_id=d.id, version_no=2, content_hash="h2", clean_text="v2", effective_at=t2
    )
    db.add_all([v1, v2])
    db.flush()

    # In V1: P1 comes first in document order (char_start=0) and has moderate similarity to C_target.
    # P2 comes second in document order (char_start=50) and is byte-identical to C_target!
    p1 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v1.id,
        statement="City transit report showed strong performance.",
        quote="City transit report showed strong performance.",
        char_start=0,
        char_end=46,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_p1",
        status="active",
        embedding=[0.2] * 384,
    )
    p2 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v1.id,
        statement="Downtown traffic fell by 18%.",
        quote="Downtown traffic fell by 18%.",
        char_start=50,
        char_end=78,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_p2",
        status="active",
        embedding=[0.1] * 384,
    )
    # In V2: only one claim C_target, identical to p2
    c_target = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v2.id,
        statement="Downtown traffic fell by 18%.",
        quote="Downtown traffic fell by 18%.",
        char_start=0,
        char_end=28,
        asserted_at=t2,
        observed_at=t2,
        claim_hash="h_ct",
        status="active",
        embedding=[0.1] * 384,
    )
    db.add_all([p1, p2, c_target])
    db.flush()

    reconcile_document(db, d.id)
    db.flush()

    db.refresh(p1)
    db.refresh(p2)
    # p2 must be carried and supersede c_target; p1 must be retracted!
    assert p2.status == "carried"
    assert p2.superseded_by == c_target.id
    assert p1.status == "retracted"


@pytest.mark.db
def test_reconcile_unconflicted_supersede_threshold(db, user_factory):
    """DOM-18: Non-conflicting rewrite with similarity < 0.70 must not supersede."""
    from app.models.claim import Claim
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.temporal.reconcile import reconcile_document

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    v1 = DocumentVersion(
        user_id=user_id, document_id=d.id, version_no=1, content_hash="h1", clean_text="v1", effective_at=t1
    )
    v2 = DocumentVersion(
        user_id=user_id, document_id=d.id, version_no=2, content_hash="h2", clean_text="v2", effective_at=t2
    )
    db.add_all([v1, v2])
    db.flush()

    # Cosine similarity around ~0.55
    emb1 = [1.0] + [0.0] * 383
    emb2 = [0.55] + [0.83516] + [0.0] * 382
    p1 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v1.id,
        statement="The team achieved notable progress in project execution.",
        quote="The team achieved notable progress in project execution.",
        char_start=0,
        char_end=56,
        asserted_at=t1,
        observed_at=t1,
        claim_hash="h_p1",
        status="active",
        embedding=emb1,
    )
    c1 = Claim(
        user_id=user_id,
        document_id=d.id,
        version_id=v2.id,
        statement="The committee reviewed substantial findings in program work.",
        quote="The committee reviewed substantial findings in program work.",
        char_start=0,
        char_end=60,
        asserted_at=t2,
        observed_at=t2,
        claim_hash="h_c1",
        status="active",
        embedding=emb2,
    )
    db.add_all([p1, c1])
    db.flush()

    reconcile_document(db, d.id)
    db.flush()

    db.refresh(p1)
    # Similarity is 0.55, which is < 0.70. With no number/date conflict, p1 must NOT be superseded.
    assert p1.status == "retracted"


@pytest.mark.db
def test_cited_evidence_finds_match_in_later_sentence(db, user_factory):
    """DOM-5: In cited mode, evidence must be scored against the sentence, finding facts past sentence 2."""
    from app.models.chunk import Chunk
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.verify.verifier import verify_text

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    # 5 sentences total. The 5th sentence contains the fact.
    sentences = [
        "First background sentence with introductory context.",
        "Second unrelated sentence about market conditions.",
        "Third sentence detailing team background and operations.",
        "Fourth sentence describing equipment and test facilities.",
        "The HX-2 battery pack delivers an energy density of 450 Wh/kg.",
    ]
    full_text = " ".join(sentences)
    ver = DocumentVersion(
        user_id=user_id,
        document_id=d.id,
        version_no=1,
        content_hash="h1",
        clean_text=full_text,
        effective_at=t1,
    )
    db.add(ver)
    db.flush()

    chunk = Chunk(
        user_id=user_id,
        document_id=d.id,
        version_id=ver.id,
        chunk_index=0,
        text=full_text,
        char_start=0,
        char_end=len(full_text),
        chunk_hash="h_chunk_1",
        embedding=[0.1] * 384,
    )
    db.add(chunk)
    db.flush()

    # Query matching sentence 5
    query = "The HX-2 battery pack delivers an energy density of 450 Wh/kg."
    result = verify_text(
        db,
        user_id,
        query,
        citations={1: [chunk.id]},
    )
    assert len(result.sentences) == 1
    # Before fix: only sentences 1 & 2 are returned, fact is missed -> unsupported.
    # With fix: sentence 5 is scored and returned -> supported.
    assert result.sentences[0].verdict == "supported"


@pytest.mark.db
def test_cited_mode_uncited_sentence_does_not_call_hybrid_search(db, user_factory, monkeypatch):
    """DOM-6: In cited mode, an uncited sentence must not fall back to hybrid_search."""
    import app.retrieval.hybrid as hybrid_mod
    from app.models.chunk import Chunk
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.verify.verifier import verify_text

    called = []

    def fail_search(*args, **kwargs):
        called.append(True)
        raise RuntimeError("hybrid_search should not have been called!")

    monkeypatch.setattr(hybrid_mod, "hybrid_search", fail_search)

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    ver = DocumentVersion(
        user_id=user_id,
        document_id=d.id,
        version_no=1,
        content_hash="h1",
        clean_text="Test",
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(ver)
    db.flush()

    chunk = Chunk(
        user_id=user_id,
        document_id=d.id,
        version_id=ver.id,
        chunk_index=0,
        text="Test",
        char_start=0,
        char_end=4,
        chunk_hash="h_chunk_2",
        embedding=[0.1] * 384,
    )
    db.add(chunk)
    db.flush()

    # Two sentences. Sentence 1 is cited, Sentence 2 has NO citation.
    text = "First cited sentence. Second completely uncited sentence."
    result = verify_text(
        db,
        user_id,
        text,
        citations={1: [chunk.id]},
    )
    assert not called, "hybrid_search must not be called in cited mode for uncited sentence"
    assert len(result.sentences) == 2
    s2 = result.sentences[1]
    assert s2.verdict == "unsupported"
    assert "no_citation" in s2.flags


def test_verify_prompt_renders_without_undefined_error():
    """LLM-1: Verify prompt must render successfully with StrictUndefined without attribute errors."""
    from app.llm.prompts import render
    from app.llm.schemas import VerifyResponse

    items = [
        {
            "index": 1,
            "text": "The HX-2 cell delivers 450 Wh/kg.",
            "evidence": [
                {
                    "id": 1,
                    "source": "TechCrunch",
                    "date": "2026-03-01",
                    "text": "Cell delivers 450 Wh/kg.",
                }
            ],
        }
    ]
    messages, version = render("verify", schema=VerifyResponse, items=items)
    assert len(messages) == 2
    assert "Cell delivers 450 Wh/kg." in messages[1].content


@pytest.mark.db
def test_verify_llm_degrades_when_zero_evidence_cited(db, user_factory):
    """LLM-1: When LLM returns supported/contradicted with zero cited IDs, degrade to heuristic."""
    import json

    from app.llm.providers.fake import get_fake_provider
    from app.models.chunk import Chunk
    from app.models.document import Document, DocumentVersion
    from app.models.source import Source
    from app.verify.verifier import verify_text

    _, me = user_factory()
    user_id = uuid.UUID(me["id"])

    src = Source(user_id=user_id, name="Test Source", source_type="web")
    db.add(src)
    db.flush()

    d = Document(user_id=user_id, source_id=src.id, title="Doc", identity_key=f"doc:{uuid.uuid4().hex}")
    db.add(d)
    db.flush()

    ver = DocumentVersion(
        user_id=user_id,
        document_id=d.id,
        version_no=1,
        content_hash="h1",
        clean_text="Completely unrelated content.",
        effective_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(ver)
    db.flush()

    chunk = Chunk(
        user_id=user_id,
        document_id=d.id,
        version_id=ver.id,
        chunk_index=0,
        text="Completely unrelated content.",
        char_start=0,
        char_end=28,
        chunk_hash="h_chunk_3",
        embedding=[0.1] * 384,
    )
    db.add(chunk)
    db.flush()

    # Script fake LLM to return supported with supporting_ids=[] (zero citations)
    fake_response = {
        "results": [
            {
                "sentence_index": 1,
                "verdict": "supported",
                "supporting_ids": [],
                "contradicting_ids": [],
                "confidence": 0.99,
                "rationale": "I hallucinated this support with no evidence citation",
            }
        ]
    }
    provider = get_fake_provider()
    provider.script("verify", [json.dumps(fake_response)])
    try:
        result = verify_text(
            db,
            user_id,
            "Target sentence to verify.",
            mode="smart",
            use_llm=True,
            citations={1: [chunk.id]},
        )
        s = result.sentences[0]
        # Must NOT accept the hallucinated 'supported'! Must degrade to heuristic.
        assert s.verdict == "unsupported"
        assert "llm_missing_citation" in s.flags
    finally:
        provider.clear_script("verify")


# --- DOM-26: date extraction, event words, update cues ---------------


def test_month_only_english_date_uses_asserted_year_anchor():
    """DOM-26: 'in July' with an asserted_at of 2026 is month precision in 2026."""
    from app.claims.numbers import extract_dates

    dates = extract_dates("Production will begin in July.", asserted_at=datetime(2026, 1, 5, tzinfo=UTC))
    assert [(d.year, d.month, d.day) for d in dates] == [(2026, 7, None)]

    dates = extract_dates("The factory will launch in March.", asserted_at=datetime(2027, 2, 1, tzinfo=UTC))
    assert [(d.year, d.month, d.day) for d in dates] == [(2027, 3, None)]


def test_month_only_chinese_date_uses_asserted_year_anchor():
    """DOM-26: '7 月' with an asserted_at of 2026 is month precision in 2026."""
    from app.claims.numbers import extract_dates

    dates = extract_dates("量产计划在 7 月启动。", asserted_at=datetime(2026, 3, 1, tzinfo=UTC))
    assert [(d.year, d.month, d.day) for d in dates] == [(2026, 7, None)]


def test_month_only_date_without_anchor_is_not_guessed():
    """DOM-26: With no asserted_at there is no year to anchor on, so nothing is extracted."""
    from app.claims.numbers import extract_dates

    assert extract_dates("Production will begin in July.") == []
    assert extract_dates("量产计划在 7 月启动。") == []


def test_event_words_scheduled_opening_and_chinese_opening_words():
    """DOM-26: New event words are recognised, and they decide date conflicts."""
    from app.claims.numbers import _has_event_word
    from app.claims.signals import date_conflict

    assert _has_event_word("The line is scheduled for completion.") is True
    assert _has_event_word("The bridge opening is set for next year.") is True
    assert _has_event_word("大桥将于 2027 年通车。") is True
    assert date_conflict("The plant is scheduled for 2027.", "The plant is scheduled for 2028.") is True


def test_update_cues_deferred_rescheduled_and_zh_original_schedule():
    """DOM-26: deferred, rescheduled and 原定 are update cues; postponed to and 推迟至 remain."""
    from app.claims.signals import temporal_update_cue

    assert temporal_update_cue("The rollout was deferred.") is True
    assert temporal_update_cue("The launch was rescheduled for March.") is True
    assert temporal_update_cue("Launch postponed to 2027.") is True
    assert temporal_update_cue("量产原定 2026 年 9 月完成。") is True
    assert temporal_update_cue("交付延至 2028 年。") is True
    assert temporal_update_cue("量产推迟至 2027 年。") is True


def test_year_without_event_word_is_not_a_date():
    """DOM-26: A bare year in a sentence with no event word is not extracted."""
    from app.claims.numbers import extract_dates

    assert extract_dates("The consortium was founded in 2027.") == []


def test_end_does_not_match_inside_endorse():
    """DOM-26: The event word 'ends' must not match inside 'endorse' (no false event)."""
    from app.claims.numbers import _has_event_word, extract_dates

    assert _has_event_word("The board will endorse the plan in 2027.") is False
    assert extract_dates("The board will endorse the plan in 2027.") == []
    assert _has_event_word("The contract ends in 2027.") is True
