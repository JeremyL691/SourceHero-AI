"""M7.6: the verifier's four verdicts, the signals behind them, and the Ask stream.

The verifier tests run against the real demo corpus (`demo_db`), committed through its
own connection, because the four expected verdicts are properties of the corpus and
the thresholds together -- neither can be tested in isolation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.claims.numbers import extract_dates, extract_quantities
from app.claims.signals import (
    date_conflict,
    identical_normalized,
    negation_asymmetry,
    numbers_conflict,
    temporal_update_cue,
    token_f1,
)

pytestmark = pytest.mark.db

# The plan's four sentences and the verdicts it fixes for them.
EXPECTED_VERDICTS = [
    ("The HX-2 cell delivers an energy density of 450 Wh/kg.", "disputed"),
    ("Orchid-7 was trained on 2 trillion tokens.", "supported"),
    ("Orchid-7 was trained on 5 trillion tokens.", "contradicted"),
    ("Orchid-7 can write poetry in Latin.", "unsupported"),
]


@pytest.fixture(scope="module")
def demo_user(engine):
    """A real, committed user whose corpus is the demo corpus.

    Module-scoped because seeding and indexing the corpus per test would dominate the
    suite's runtime, and nothing here mutates it. A real user row (rather than a bare
    UUID) is what lets the API tests authenticate as this account without having to
    re-point ownership afterwards.
    """
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.demo.seed import seed_demo, summary
    from app.indexing.indexer import index_version
    from app.jobs.handlers import register_all
    from app.models.document import DocumentVersion
    from app.models.user import User

    register_all()
    settings = get_settings()
    original_inline = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    try:
        user = User(
            id=user_id,
            email=f"verify{uuid.uuid4().hex[:10]}@test.local",
            password_hash=hash_password("verify-password-123"),
            is_admin=False,
        )
        session.add(user)
        session.commit()

        seed_demo(session, user_id)
        session.commit()
        for version in session.query(DocumentVersion).filter(DocumentVersion.user_id == user_id).all():
            index_version(session, version.id)
        session.commit()
        assert summary(session, user_id)["documents"] == 12

        yield user_id

        session.rollback()
        for table in ("chunks", "document_versions", "documents", "sources"):
            session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
        # seed_demo commits queued index_version jobs. Leaving them behind would let
        # the queue tests dequeue someone else's job and fail for the wrong reason.
        session.execute(text("DELETE FROM jobs WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM answers WHERE user_id = :u"), {"u": user_id})
        session.execute(text("DELETE FROM verifications WHERE user_id = :u"), {"u": user_id})
        # `users` keys on `id`, not `user_id`.
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        session.commit()
    finally:
        session.close()
        settings.jobs_inline = original_inline


@pytest.fixture
def demo_db(engine):
    from sqlalchemy.orm import Session

    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def demo_client(engine, demo_user):
    """A TestClient authenticated as the seeded demo user."""
    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.core.security import create_access_token
    from app.main import create_app

    app = create_app()
    session = engine.connect()
    from sqlalchemy.orm import Session as OrmSession

    orm = OrmSession(bind=session)
    app.dependency_overrides[get_db] = lambda: orm
    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {create_access_token(demo_user)}"})
            yield client
    finally:
        app.dependency_overrides.clear()
        orm.close()
        session.close()


# --- numbers and dates (pure) -------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The HX-2 cell delivers an energy density of 450 Wh/kg.", [(450.0, "wh/kg")]),
        ("HX-2 pack prices of $120/kWh at launch.", [(120.0, "usd/kwh")]),
        ("reduced downtown traffic by 18% in its first year.", [(18.0, "%")]),
        ("The congestion charge costs drivers $9 per day.", [(9.0, "usd/day")]),
        ("Orchid-7 has 7 billion parameters.", [(7e9, "params")]),
        ("Orchid-7 was trained on 2 trillion tokens.", [(2e12, "tokens")]),
        ("after 1,000 charge cycles.", [(1000.0, "cycles")]),
        ("The fee is 120 美元 per day.", [(120.0, "usd/day")]),
    ],
)
def test_quantity_forms_the_plan_names(text, expected):
    got = [(round(q.value, 4), q.unit) for q in extract_quantities(text)]
    assert got == expected


def test_unitless_numbers_are_ignored():
    """ "Chapter 3" and "the second version" are not measurements."""
    assert extract_quantities("Chapter 3 explains the second version.") == []


def test_currency_forms_share_a_unit():
    """$9 per day, $9/day and 9 USD/day must compare as one quantity."""
    units = {
        extract_quantities(text)[0].unit
        for text in ("costs $9 per day", "costs $9/day", "costs 9 USD per day")
    }
    assert units == {"usd/day"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Production will begin in September 2026.", ("September 2026", (2026, 900))),
        ("Production will begin in January 2027.", ("January 2027", (2027, 100))),
        ("计划于 2026 年 9 月开始量产。", ("2026 年 9 月", (2026, 900))),
        ("shipped in Q1 2027", ("Q1 2027", (2027, 100))),
        ("dated 2026-07-01", ("2026-07-01", (2026, 701))),
    ],
)
def test_date_forms(text, expected):
    dates = extract_dates(text)
    assert (dates[0].raw, dates[0].key) == expected


def test_bare_year_needs_an_event_word():
    assert extract_dates("Launched in 2026.")
    assert extract_dates("The 2026 model year saw growth.") == []


def test_equivalent_spellings_of_a_month_collide():
    a = extract_dates("Production will begin in September 2026.")[0]
    b = extract_dates("Mass production starts 2026-09.")[0]
    assert a.month_key == b.month_key


# --- signals -------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("delivers 450 Wh/kg", "measured 380 Wh/kg", True),
        ("delivers 450 Wh/kg", "It delivers 450 Wh/kg", False),
        ("450 Wh/kg", "$120/kWh", None),
        ("retained 92%", "retained 95%", False),
        ("retained 80%", "retained 95%", True),
    ],
)
def test_numbers_conflict(a, b, expected):
    assert numbers_conflict(a, b) is expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("The charge did not reduce traffic.", "The charge reduced traffic.", True),
        ("A did not reduce.", "B did not reduce.", False),
        ("该措施没有减少交通。", "该措施减少了交通。", True),
        ("该措施没有减少交通。", "公交没有增加班次。", False),
    ],
)
def test_negation_asymmetry(a, b, expected):
    assert negation_asymmetry(a, b) is expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Production will begin in September 2026.", "Production will begin in January 2027.", True),
        ("Production will begin in September 2026.", "Mass production starts 2026-09.", False),
        ("The report is dated 2026-01-01.", "The review is dated 2027-02-01.", None),
        ("将于 2026 年 9 月开始量产。", "将于 2027 年 1 月开始量产。", True),
    ],
)
def test_date_conflict(a, b, expected):
    assert date_conflict(a, b) is expected


def test_temporal_update_cue():
    assert temporal_update_cue("has been postponed to January 2027")
    assert temporal_update_cue("量产推迟到 2027 年 1 月")
    assert not temporal_update_cue("delivers 450 Wh/kg.")


def test_token_f1_orders_sensibly():
    a = "the hx-2 cell delivers 450 wh/kg"
    assert token_f1(a, a) == pytest.approx(1.0)
    same_topic = token_f1(a, "independent tests measured the hx-2 energy density at 380 wh/kg")
    unrelated = token_f1(a, "the ferry timetable lists sailings across the strait")
    assert same_topic > unrelated
    assert unrelated == 0.0


def test_identical_normalized_ignores_case_and_trailing_punctuation():
    assert identical_normalized("The HX-2 delivers 450 Wh/kg.", "the hx-2 delivers 450 wh/kg!")
    assert not identical_normalized("delivers 450 Wh/kg", "delivers 380 Wh/kg")


# --- the four verdicts ---------------------------------------------------


@pytest.mark.parametrize(("sentence", "verdict"), EXPECTED_VERDICTS)
def test_expected_verdicts_on_the_demo_corpus(demo_db, demo_user, sentence, verdict):
    from app.verify.verifier import verify_text

    result = verify_text(demo_db, demo_user, sentence)
    assert len(result.sentences) == 1, f"expected one sentence, got {len(result.sentences)}"
    assert result.sentences[0].verdict == verdict, (
        f"{sentence!r}: got {result.sentences[0].verdict} ({result.sentences[0].rationale})"
    )
    assert result.summary[verdict] == 1


def test_disputed_explains_both_sides(demo_db, demo_user):
    """`disputed` must carry the supporting and the contradicting evidence."""
    from app.verify.verifier import verify_text

    result = verify_text(demo_db, demo_user, EXPECTED_VERDICTS[0][0])
    sentence = result.sentences[0]
    assert sentence.supporting, "a disputed sentence needs a supporting citation"
    assert sentence.contradicting, "a disputed sentence needs a contradicting citation"
    assert sentence.supporting[0].text != sentence.contradicting[0].text


def test_evidence_offsets_address_the_real_text(demo_db, demo_user):
    """Every reported span must slice the version text it claims to come from."""
    from app.models.document import DocumentVersion
    from app.verify.verifier import verify_text

    result = verify_text(demo_db, demo_user, EXPECTED_VERDICTS[1][0])
    for span in result.sentences[0].supporting:
        version = demo_db.get(DocumentVersion, span.version_id)
        assert version is not None
        assert version.clean_text[span.char_start : span.char_end] == span.text


def test_summary_counts_every_verdict(demo_db, demo_user):
    from app.verify.verifier import verify_text

    result = verify_text(
        demo_db,
        demo_user,
        "Orchid-7 was trained on 2 trillion tokens. Orchid-7 can write poetry in Latin.",
    )
    assert result.summary["supported"] == 1
    assert result.summary["unsupported"] == 1
    assert sum(result.summary.values()) == 2


def test_empty_text_is_not_an_error(demo_db, demo_user):
    from app.verify.verifier import verify_text

    assert verify_text(demo_db, demo_user, "   ").sentences == []


def test_oversized_text_is_rejected(demo_db, demo_user):
    from app.core.errors import DomainValidationError
    from app.verify.config import MAX_VERIFY_CHARS
    from app.verify.verifier import verify_text

    with pytest.raises(DomainValidationError):
        verify_text(demo_db, demo_user, "x " * (MAX_VERIFY_CHARS // 2 + 10))


def test_verification_never_crosses_tenants(demo_db, demo_user):
    from app.verify.verifier import verify_text

    result = verify_text(demo_db, uuid.uuid4(), "Orchid-7 was trained on 2 trillion tokens.")
    assert result.sentences[0].verdict == "unsupported", "another tenant's evidence must be invisible"


def test_cited_mode_only_considers_the_cited_chunks(demo_db, demo_user):
    """A cited-mode sentence may not pass on evidence it did not cite."""
    from app.models.chunk import Chunk
    from app.verify.verifier import verify_text

    # Must be the chunks that actually carry the sentence's evidence: citing any two
    # random chunks would (correctly) leave the sentence unsupported.
    chunk_ids = list(
        demo_db.execute(
            select(Chunk.id).where(Chunk.user_id == demo_user, Chunk.text.contains("2 trillion"))
        ).scalars()
    )
    assert chunk_ids, "the demo corpus must contain a chunk with this evidence"
    result = verify_text(
        demo_db,
        demo_user,
        "Orchid-7 was trained on 2 trillion tokens.",
        citations={1: chunk_ids},
    )
    assert result.sentences[0].verdict == "supported"


def test_citing_a_chunk_that_does_not_exist_is_flagged(demo_db, demo_user):
    from app.verify.verifier import verify_text

    result = verify_text(
        demo_db, demo_user, "Orchid-7 was trained on 2 trillion tokens.", citations={1: [99999999]}
    )
    sentence = result.sentences[0]
    assert sentence.verdict == "unsupported"
    assert "invalid_citation" in sentence.flags
    assert sentence.cited_ok is False


# --- the API -------------------------------------------------------------
def test_verify_endpoint(demo_client):
    resp = demo_client.post("/v1/verify", json={"text": EXPECTED_VERDICTS[1][0]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sentences"][0]["verdict"] == "supported"
    assert body["summary"]["supported"] == 1
    assert body["sentences"][0]["supporting"], "verdicts must carry their evidence"


def test_verify_endpoint_persists(demo_client, engine):
    from sqlalchemy.orm import Session

    from app.models.answer import Verification

    demo_client.post("/v1/verify", json={"text": "Orchid-7 was trained on 2 trillion tokens."})
    with Session(bind=engine) as session:
        assert session.query(Verification).count() >= 1


def test_verify_rejects_oversized_input(demo_client):
    assert demo_client.post("/v1/verify", json={"text": "x" * 9000}).status_code == 422
