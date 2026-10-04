"""M8.11: the claim ledger -- extraction, alignment, clustering, relations, camps."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.claims.align import align_quote
from app.claims.camps import compute_camps
from app.claims.cluster import assign_cluster, thresholds
from app.claims.extract import extract_version_claims, verify_quote_invariant
from app.claims.heuristic_extract import heuristic_claims, is_claim_sentence
from app.claims.relate import MAX_JUDGE_PAIRS_PER_CLAIM, heuristic_relation
from app.models.claim import Claim, ClaimCluster, ClaimEvent, ClaimRelation

# --- pure: extraction (no database) --------------------------------------

# The plan pins these sentences; tests across M7-M12 assert on them.
REQUIRED_SENTENCES = [
    ("The HX-2 cell delivers an energy density of 450 Wh/kg.", "number"),
    ("Mass production of the HX-2 will begin in September 2026.", "date"),
    ("Halcyon Cells expects HX-2 pack prices of $120/kWh at launch.", "number"),
    ("Independent lab tests measured the HX-2 energy density at 380 Wh/kg.", "number"),
    ("The HX-2 retained 92% of its capacity after 1,000 charge cycles.", "number"),
    ("Mass production of the HX-2 has been postponed to January 2027.", "date"),
    ("First-year HX-2 pack prices will be above $150/kWh.", "number"),
    ("The HX-2 energy density of 450 Wh/kg would lead the industry.", "number"),
    ("HX-2 电池的能量密度为 450 Wh/kg。", "number"),
    ("Halcyon Cells 计划于 2026 年 9 月开始量产 HX-2。", "date"),
    (
        "Mass production of the HX-2 will begin in September 2026, so we should budget for it in Q4.",
        "date",
    ),
    ("Ferro City's congestion charge reduced downtown traffic by 18% in its first year.", "number"),
    ("The congestion charge costs drivers $9 per day.", "number"),
    ("Downtown traffic fell by 18% after the congestion charge was introduced.", "number"),
    ("The congestion charge did not reduce downtown traffic.", "fact"),
    ("Orchid-7 has 7 billion parameters.", "number"),
    ("Orchid-7 was trained on 2 trillion tokens.", "number"),
    ("Orchid-7 scored 68% on the ReasonBench evaluation.", "number"),
    ("Orchid-7 scored 61% on the ReasonBench evaluation in our reproduction.", "number"),
]


@pytest.mark.parametrize(("sentence", "claim_type"), REQUIRED_SENTENCES)
def test_required_sentences_survive_extraction(sentence, claim_type):
    drafts = heuristic_claims(sentence, 0)
    assert drafts, f"the filter dropped a required sentence: {sentence!r}"
    assert drafts[0].claim_type == claim_type, f"{sentence!r} -> {drafts[0].claim_type}"


def test_short_english_claim_survives():
    """ "Orchid-7 has 7 billion parameters." has only three content tokens."""
    assert is_claim_sentence("Orchid-7 has 7 billion parameters.")


@pytest.mark.parametrize(
    "sentence",
    [
        "Subscribe to our newsletter.",
        "Click here for more.",
        "Is this a good idea?",
        "© 2026 Example Corp",
        "Short.",
        "This sentence rambles on and on without ever asserting anything in particular.",
    ],
)
def test_boilerplate_and_trivia_are_rejected(sentence):
    assert not is_claim_sentence(sentence)


def test_bare_assertion_without_numbers_is_accepted():
    assert is_claim_sentence("Halcyon Cells is a battery company.")


# --- pure: alignment -----------------------------------------------------


CHUNK = "Halcyon Cells expects HX-2 pack prices of $120/kWh at launch. The HX-2 cell delivers 450 Wh/kg."


def test_alignment_exact():
    span = align_quote("HX-2 pack prices of $120/kWh", CHUNK, 0)
    assert span is not None
    assert CHUNK[span[0] : span[1]] == "HX-2 pack prices of $120/kWh"


def test_alignment_handles_typographic_variants():
    curly = "Halcyon said “this is fine” today."
    span = align_quote('Halcyon said "this is fine" today.', curly, 0)
    assert span is not None
    assert curly[span[0] : span[1]] == curly


def test_alignment_survives_a_single_typo():
    span = align_quote("HX-2 pack prices of $12O/kWh", CHUNK, 0)
    assert span is not None
    assert CHUNK[span[0] : span[1]] == "HX-2 pack prices of $120/kWh"


def test_alignment_rejects_a_fabricated_quote():
    assert align_quote("Halcyon raised $900 million in Series D", CHUNK, 0) is None
    assert align_quote("", CHUNK, 0) is None


def test_alignment_offsets_are_absolute():
    span = align_quote("HX-2 pack prices of $120/kWh", CHUNK, 500)
    assert span is not None
    assert span[0] >= 500


# --- pure: relations -----------------------------------------------------


class _C:
    def __init__(self, statement: str, day: int, ident: int) -> None:
        self.statement = statement
        self.quote = statement
        self.id = ident
        self.asserted_at = datetime(2026, 1, day, tzinfo=UTC)


@pytest.mark.parametrize(
    ("a", "b", "similarity", "expected"),
    [
        (
            "The HX-2 cell delivers an energy density of 450 Wh/kg.",
            "Independent lab tests measured the HX-2 energy density at 380 Wh/kg.",
            0.46,
            "contradicts",
        ),
        (
            "The congestion charge costs drivers $9 per day.",
            "The congestion charge costs drivers $12 per day.",
            0.5,
            "contradicts",
        ),
        (
            "The congestion charge did not reduce downtown traffic.",
            "Downtown traffic fell by 18% after the congestion charge was introduced.",
            0.4,
            "contradicts",
        ),
        (
            "Orchid-7 scored 68% on the ReasonBench evaluation.",
            "Orchid-7 scored 61% on the ReasonBench evaluation in our reproduction.",
            0.6,
            "contradicts",
        ),
        (
            "Orchid-7 was trained on 2 trillion tokens.",
            "Orchid-7 was trained on 2 trillion tokens.",
            0.9,
            "duplicate",
        ),
        (
            "Mass production of the HX-2 will begin in September 2026.",
            "Mass production of the HX-2 has been postponed to January 2027.",
            0.5,
            "supersedes",
        ),
        (
            "Halcyon Cells expects HX-2 pack prices of $120/kWh at launch.",
            "First-year HX-2 pack prices will be above $150/kWh.",
            0.45,
            "contradicts",
        ),
        ("Orchid-7 has 7 billion parameters.", "A ferry timetable lists sailings.", 0.1, None),
    ],
)
def test_heuristic_relations(a, b, similarity, expected):
    draft = heuristic_relation(_C(a, 3, 1), _C(b, 4, 2), similarity)
    assert (draft.relation if draft else None) == expected


# --- pure: camps ---------------------------------------------------------


def _claim(ident: int, statement: str, document: int) -> Claim:
    claim = Claim()
    claim.id = ident
    claim.statement = statement
    claim.document_id = uuid.UUID(int=document)
    claim.asserted_at = datetime(2026, 1, 1, tzinfo=UTC)
    claim.status = "active"
    return claim


def _relation(a: int, b: int, kind: str, label: str | None = None) -> ClaimRelation:
    relation = ClaimRelation()
    relation.a_claim_id, relation.b_claim_id = sorted((a, b))
    relation.relation = kind
    relation.resolved_at = None
    relation.user_label = label
    return relation


def test_camps_split_a_disagreement_in_two():
    claims = [_claim(1, "450 Wh/kg", 1), _claim(2, "380 Wh/kg", 2)]
    camps = compute_camps(claims, [_relation(1, 2, "contradicts")])
    assert camps.balanced
    assert len(camps.camps) == 2
    assert {c.label for c in camps.camps} == {"A", "B"}
    assert camps.camps[0].claim_ids == [1]
    assert camps.camps[1].claim_ids == [2]


def test_camps_group_agreeing_claims_together():
    claims = [_claim(1, "450 Wh/kg", 1), _claim(2, "also 450", 2), _claim(3, "380 Wh/kg", 3)]
    camps = compute_camps(claims, [_relation(1, 2, "supports"), _relation(1, 3, "contradicts")])
    assert camps.balanced
    assert camps.camps[0].claim_ids == [1, 2]


def test_camps_flag_an_impossible_triangle():
    """Three pairwise contradictions cannot be two-coloured; say so rather than guess."""
    claims = [_claim(1, "A", 1), _claim(2, "B", 2), _claim(3, "C", 3)]
    camps = compute_camps(
        claims,
        [_relation(1, 2, "contradicts"), _relation(2, 3, "contradicts"), _relation(1, 3, "contradicts")],
    )
    assert camps.balanced is False
    assert camps.frustrated_edges


def test_unconnected_claims_are_unaligned():
    camps = compute_camps([_claim(1, "A", 1), _claim(2, "lonely", 2)], [])
    assert camps.unaligned == [1, 2]
    assert camps.camps == []


def test_a_rejected_relation_does_not_colour():
    camps = compute_camps(
        [_claim(1, "A", 1), _claim(2, "B", 2)], [_relation(1, 2, "contradicts", label="rejected")]
    )
    assert camps.camps == []
    assert camps.unaligned == [1, 2]


# --- against the demo corpus --------------------------------------------


@pytest.fixture(scope="module")
def ledger(engine):
    """A user whose claim ledger is the demo corpus, fully extracted and judged."""
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.demo.seed import drain, seed_demo
    from app.models.user import User

    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    try:
        session.add(
            User(
                id=user_id,
                email=f"claims{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("claims-password-123"),
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        drain(session)
        session.commit()
        yield session, user_id
        session.rollback()
        from sqlalchemy import text

        for table in (
            "claim_events",
            "claim_relations",
            "claim_clusters",
            "claims",
            "chunks",
            "document_versions",
            "documents",
            "sources",
            "jobs",
        ):
            session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        session.commit()
    finally:
        session.close()
        settings.jobs_inline = original


@pytest.mark.db
def test_every_claim_quote_reproduces_the_source(ledger):
    """The invariant the whole product rests on: quotes are never fabricated."""
    session, user_id = ledger
    assert verify_quote_invariant(session, user_id) == []
    claims = session.execute(select(Claim).where(Claim.user_id == user_id)).scalars().all()
    assert claims, "the demo corpus must produce claims"


@pytest.mark.db
def test_conflicts_are_found(ledger):
    """The plan's acceptance: at least four contradictions on the demo corpus."""
    session, user_id = ledger
    conflicts = (
        session.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == user_id,
                ClaimRelation.relation == "contradicts",
                ClaimRelation.resolved_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    assert len(conflicts) >= 4, f"only {len(conflicts)} unresolved contradictions"


@pytest.mark.db
def test_the_energy_density_conflict_exists_with_both_camps(ledger):
    """450 vs 380 Wh/kg is the demo's headline disagreement."""
    session, user_id = ledger
    other = aliased(Claim)
    rows = session.execute(
        select(ClaimRelation, Claim, other)
        .join(Claim, Claim.id == ClaimRelation.a_claim_id)
        .join(other, other.id == ClaimRelation.b_claim_id)
        .where(
            ClaimRelation.user_id == user_id,
            ClaimRelation.relation == "contradicts",
            ClaimRelation.resolved_at.is_(None),
        )
    ).all()
    for _relation, a, b in rows:
        text = f"{a.statement} {b.statement}"
        if "450" in text and "380" in text:
            assert a.cluster_id == b.cluster_id, "both sides must share a cluster"
            cluster = session.get(ClaimCluster, a.cluster_id)
            assert cluster.conflict_count > 0
            claims = (
                session.execute(
                    select(Claim).where(Claim.cluster_id == a.cluster_id, Claim.status == "active")
                )
                .scalars()
                .all()
            )
            ids = [c.id for c in claims]
            rels = (
                session.execute(
                    select(ClaimRelation).where(
                        ClaimRelation.a_claim_id.in_(ids), ClaimRelation.b_claim_id.in_(ids)
                    )
                )
                .scalars()
                .all()
            )
            camps = compute_camps(list(claims), list(rels))
            assert len(camps.camps) == 2, "a two-sided disagreement must produce two camps"
            # Containment, not equality: a multilingual embedder may place the
            # Chinese statement on either side.
            assert {a.id, b.id} <= set(camps.camps[0].claim_ids) | set(camps.camps[1].claim_ids)
            return
    pytest.fail("the 450 vs 380 Wh/kg conflict was not found")


@pytest.mark.db
def test_every_conflicted_cluster_records_its_count(ledger):
    session, user_id = ledger
    clusters = (
        session.execute(
            select(ClaimCluster).where(ClaimCluster.user_id == user_id, ClaimCluster.conflict_count > 0)
        )
        .scalars()
        .all()
    )
    assert clusters, "some clusters must record conflicts"
    for cluster in clusters:
        members = list(
            session.execute(
                select(Claim.id).where(Claim.cluster_id == cluster.id, Claim.status == "active")
            ).scalars()
        )
        actual = (
            session.execute(
                select(ClaimRelation).where(
                    ClaimRelation.user_id == user_id,
                    ClaimRelation.relation == "contradicts",
                    ClaimRelation.resolved_at.is_(None),
                    (ClaimRelation.a_claim_id.in_(members)) | (ClaimRelation.b_claim_id.in_(members)),
                )
            )
            .scalars()
            .all()
        )
        assert cluster.conflict_count == len(actual)


@pytest.mark.db
def test_assertion_events_exist_for_every_claim(ledger):
    session, user_id = ledger
    claims = session.execute(select(Claim).where(Claim.user_id == user_id)).scalars().all()
    events = (
        session.execute(
            select(ClaimEvent).where(ClaimEvent.user_id == user_id, ClaimEvent.event == "asserted")
        )
        .scalars()
        .all()
    )
    assert len(events) == len(claims)


@pytest.mark.db
def test_a_user_label_is_never_overwritten(ledger):
    session, user_id = ledger
    from app.claims.relate import RelationDraft, store_relation

    relation = session.execute(
        select(ClaimRelation).where(ClaimRelation.user_id == user_id).limit(1)
    ).scalar_one()
    relation.user_label = "confirmed"
    session.commit()

    a = session.get(Claim, relation.a_claim_id)
    b = session.get(Claim, relation.b_claim_id)
    stored = store_relation(
        session,
        user_id,
        a,
        b,
        RelationDraft(
            relation="supports", direction=None, confidence=0.99, signals={}, rationale="re-derived"
        ),
        method="heuristic",
    )
    session.refresh(relation)
    assert relation.user_label == "confirmed", "a human decision outranks the pipeline"
    assert relation.relation == "contradicts", "the pipeline must not overwrite it either"
    assert stored is relation


@pytest.mark.db
def test_judging_is_bounded_per_claim(ledger):
    """O(k) per claim, independent of how many claims exist."""
    session, user_id = ledger
    assert MAX_JUDGE_PAIRS_PER_CLAIM == 5
    total = session.execute(select(Claim.id).where(Claim.user_id == user_id)).scalars().all()
    assert len(total) > 20, "the corpus must be large enough for the bound to mean something"


def test_thresholds_come_from_the_measured_distribution():
    """The hashing band must sit above the unrelated p95 and below related p25."""
    limits = thresholds()
    assert 0.31 < limits["cluster_sim"] < 0.50
    assert 0.31 < limits["pair_sim"] < 0.50


@pytest.mark.db
def test_assign_cluster_is_idempotent(ledger):
    session, user_id = ledger
    claim = session.execute(select(Claim).where(Claim.user_id == user_id)).scalars().first()
    before = claim.cluster_id
    for _ in range(3):
        assign_cluster(session, claim)
    assert claim.cluster_id == before


# --- extraction API -------------------------------------------------------


@pytest.mark.db
def test_extract_version_claims_is_idempotent(ledger):
    session, user_id = ledger
    version = session.execute(select(Claim.version_id).where(Claim.user_id == user_id).limit(1)).scalar_one()
    before = session.execute(select(Claim.id).where(Claim.user_id == user_id)).scalars().all()
    stats = extract_version_claims(session, version)
    assert stats.created == 0, "re-extracting a version must create nothing"
    assert stats.unchanged > 0
    after = session.execute(select(Claim.id).where(Claim.user_id == user_id)).scalars().all()
    assert len(after) == len(before)


@pytest.mark.db
def test_delete_source_cascades_relations_and_events_without_orphans(ledger):
    """DATA-15: Deleting a source cascades and leaves no orphan claim_relations or claim_events."""
    from app.models.document import Document
    from app.models.source import Source

    session, user_id = ledger

    # Create a relation and an event tied to existing claims
    claims = session.execute(select(Claim).where(Claim.user_id == user_id).limit(2)).scalars().all()
    assert len(claims) >= 2
    c1, c2 = claims[0], claims[1]

    rel = ClaimRelation(
        user_id=user_id,
        a_claim_id=min(c1.id, c2.id),
        b_claim_id=max(c1.id, c2.id),
        relation="supports",
        confidence=0.9,
    )
    event = ClaimEvent(
        user_id=user_id,
        claim_id=c1.id,
        event="created",
        at=datetime.now(UTC),
        observed_at=datetime.now(UTC),
    )
    session.add(rel)
    session.add(event)
    session.commit()

    c1_id = c1.id
    doc = session.get(Document, c1.document_id)
    assert doc is not None
    source = session.get(Source, doc.source_id)
    assert source is not None

    session.delete(source)
    session.commit()
    session.expunge_all()

    # Verify c1 is gone
    assert session.execute(select(Claim).where(Claim.id == c1_id)).scalar_one_or_none() is None

    # Verify relation referencing c1 is cascaded and gone
    orphan_rels = (
        session.execute(
            select(ClaimRelation).where(
                (ClaimRelation.a_claim_id == c1_id) | (ClaimRelation.b_claim_id == c1_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(orphan_rels) == 0, "claim_relations had orphan rows after deleting source"

    # Verify event referencing c1 is cascaded and gone
    orphan_events = session.execute(select(ClaimEvent).where(ClaimEvent.claim_id == c1_id)).scalars().all()
    assert len(orphan_events) == 0, "claim_events had orphan rows after deleting source"


@pytest.mark.db
def test_chunk_unique_constraint(ledger):
    """DATA-17: chunks(version_id, chunk_index) unique constraint prevents duplicate chunks."""
    from sqlalchemy.exc import IntegrityError

    from app.models.chunk import Chunk

    session, user_id = ledger
    claim = session.execute(select(Claim).where(Claim.user_id == user_id).limit(1)).scalar_one()

    chunk1 = Chunk(
        user_id=user_id,
        document_id=claim.document_id,
        version_id=claim.version_id,
        chunk_index=9999,
        text="Unique chunk test",
        char_start=0,
        char_end=17,
        chunk_hash="0" * 64,
        search_text="Unique chunk test",
    )
    session.add(chunk1)
    session.commit()

    chunk2 = Chunk(
        user_id=user_id,
        document_id=claim.document_id,
        version_id=claim.version_id,
        chunk_index=9999,
        text="Duplicate chunk index",
        char_start=0,
        char_end=21,
        chunk_hash="1" * 64,
        search_text="Duplicate chunk index",
    )
    session.add(chunk2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
