"""M10.9: topic planning, compilation, validation, revisions, lint and export."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from sqlalchemy import select, text

from app.llm.schemas import WikiBody, WikiSection, WikiSentence
from app.models.claim import Claim
from app.models.wiki import LintFinding, Topic, WikiPage, WikiPageClaim
from app.wiki.compiler import compile_page, input_hash
from app.wiki.links import rebuild_links
from app.wiki.lint import lint_wiki
from app.wiki.topics import entity_keys, plan_topics, slugify
from app.wiki.validator import covers_dispute, validate_body

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def wiki(engine):
    """A user whose wiki is the compiled demo corpus."""
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.demo.seed import drain, seed_demo
    from app.jobs.handlers import register_all
    from app.models.user import User

    register_all()
    settings = get_settings()
    original = settings.jobs_inline
    settings.jobs_inline = False
    session = Session(bind=engine)
    user_id = uuid.uuid4()
    try:
        session.add(
            User(
                id=user_id,
                email=f"wiki{uuid.uuid4().hex[:10]}@test.local",
                password_hash=hash_password("wiki-password-123"),
            )
        )
        session.commit()
        seed_demo(session, user_id)
        session.commit()
        drain(session)
        session.commit()
        plan_topics(session, user_id)
        for page in _pages(session, user_id):
            compile_page(session, page)
            rebuild_links(session, page)
        session.commit()
        yield session, user_id
        session.rollback()
        # `wiki_links`, `wiki_page_claims` and `wiki_page_revisions` carry no user_id of
        # their own; they are reachable only through the pages that own them.
        session.execute(
            text(
                "DELETE FROM wiki_links WHERE from_page_id IN (SELECT id FROM wiki_pages "
                "WHERE user_id = :u) OR to_page_id IN (SELECT id FROM wiki_pages "
                "WHERE user_id = :u)"
            ),
            {"u": user_id},
        )
        session.execute(
            text(
                "DELETE FROM wiki_page_claims WHERE page_id IN (SELECT id FROM wiki_pages WHERE user_id = :u)"
            ),
            {"u": user_id},
        )
        session.execute(
            text(
                "DELETE FROM wiki_page_revisions WHERE page_id IN (SELECT id FROM wiki_pages "
                "WHERE user_id = :u)"
            ),
            {"u": user_id},
        )
        for table in (
            "lint_findings",
            "wiki_pages",
            "topics",
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


def _pages(session, user_id) -> list[WikiPage]:
    return list(
        session.execute(select(WikiPage).where(WikiPage.user_id == user_id).order_by(WikiPage.slug)).scalars()
    )


# --- pure: identity helpers ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The HX-2 cell delivers 450 Wh/kg.", {"hx-2"}),
        ("Orchid-7 has 7 billion parameters.", {"orchid-7"}),
        ("Ferro City's charge fell by 18%.", {"ferro city"}),
    ],
)
def test_entity_keys(text, expected):
    claim = Claim(statement=text)
    assert entity_keys(claim) == expected


def test_slugify_is_stable_and_lowercase():
    assert slugify("HX-2 Solid State") == "hx-2-solid-state"
    assert slugify("Ferro City") == "ferro-city"
    assert slugify("  ---  ") == "topic"


def test_input_hash_ignores_order_but_not_content():
    a = [{"id": 1, "status": "active"}, {"id": 2, "status": "superseded"}]
    b = list(reversed(a))
    assert input_hash(a, prompt_version="v1", model="heuristic") == input_hash(
        b, prompt_version="v1", model="heuristic"
    )
    assert input_hash(a, prompt_version="v1", model="heuristic") != input_hash(
        a, prompt_version="v2", model="heuristic"
    )
    changed = [{"id": 1, "status": "superseded"}, {"id": 2, "status": "superseded"}]
    assert input_hash(a, prompt_version="v1", model="heuristic") != input_hash(
        changed, prompt_version="v1", model="heuristic"
    )


# --- the demo corpus -----------------------------------------------------


def test_demo_corpus_yields_the_three_expected_topics(wiki):
    session, user_id = wiki
    topics = (
        session.execute(select(Topic).where(Topic.user_id == user_id).order_by(Topic.slug)).scalars().all()
    )
    slugs = [t.slug for t in topics]
    assert len(topics) >= 3, slugs
    assert any("hx-2" in s for s in slugs), slugs
    assert any("ferro" in s for s in slugs), slugs
    assert any("orchid" in s for s in slugs), slugs


def test_every_page_is_clean(wiki):
    session, user_id = wiki
    pages = _pages(session, user_id)
    assert pages
    assert all(p.status == "clean" for p in pages), [(p.slug, p.error) for p in pages]


def test_every_sentence_cites_at_least_one_claim(wiki):
    """The plan's core guarantee: no unsourced sentence ships."""
    session, user_id = wiki
    for page in _pages(session, user_id):
        body = page.body or {}
        sentences = list(body.get("summary") or []) + [
            s for section in (body.get("sections") or []) for s in section.get("sentences") or []
        ]
        assert sentences, f"{page.slug} has no sentences"
        for sentence in sentences:
            assert sentence.get("claim_ids"), f"{page.slug}: {sentence}"


def test_page_provenance_is_recorded(wiki):
    session, user_id = wiki
    for page in _pages(session, user_id):
        ids = list(
            session.execute(select(WikiPageClaim.claim_id).where(WikiPageClaim.page_id == page.id)).scalars()
        )
        assert ids, f"{page.slug} records no provenance"


def test_hx2_page_shows_both_sides_and_the_update(wiki):
    session, user_id = wiki
    page = session.execute(
        select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.slug == "hx-2")
    ).scalar_one_or_none()
    assert page is not None, [p.slug for p in _pages(session, user_id)]
    kinds = {section["kind"] for section in page.body.get("sections") or []}
    assert "disputed" in kinds or "history" in kinds, page.body
    text = page.body_md
    assert "450" in text and "380" in text, "the density dispute must appear on the page"


def test_history_section_reports_the_postponement(wiki):
    session, user_id = wiki
    page = session.execute(
        select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.slug == "hx-2")
    ).scalar_one()
    history = [
        s
        for section in page.body.get("sections") or []
        if section["kind"] == "history"
        for s in section.get("sentences") or []
    ]
    assert history, "the postponed schedule must be recorded as history"
    assert any("postponed to January 2027" in s["text"] for s in history)


def test_markdown_carries_footnote_citations(wiki):
    session, user_id = wiki
    for page in _pages(session, user_id):
        assert "[^1]" in page.body_md, page.slug
        assert page.body_md.startswith("# ")


def test_replanning_is_stable(wiki):
    """A re-plan must reuse topics and pages, not rename or recreate them."""
    session, user_id = wiki
    before = {t.slug: t.id for t in session.execute(select(Topic).where(Topic.user_id == user_id)).scalars()}
    pages_before = {p.slug: (p.id, p.revision) for p in _pages(session, user_id)}

    result = plan_topics(session, user_id)
    assert result.topics_created == 0
    assert result.pages_created == 0
    assert result.pages_marked_dirty == 0, "nothing changed, so nothing should be dirty"

    after = {t.slug: t.id for t in session.execute(select(Topic).where(Topic.user_id == user_id)).scalars()}
    assert after == before, "topic ids and slugs must be stable across a re-plan"
    pages_after = {p.slug: (p.id, p.revision) for p in _pages(session, user_id)}
    assert pages_after == pages_before


def test_compiling_twice_is_a_no_op(wiki):
    session, user_id = wiki
    page = _pages(session, user_id)[0]
    revision = page.revision
    result = compile_page(session, page)
    assert result.get("skipped") == "unchanged"
    assert page.revision == revision


def test_revisions_are_recorded(wiki):
    session, user_id = wiki
    page = _pages(session, user_id)[0]
    from app.models.wiki import WikiPageRevision

    rows = (
        session.execute(select(WikiPageRevision).where(WikiPageRevision.page_id == page.id)).scalars().all()
    )
    assert rows


# --- the validator (pure) ------------------------------------------------


def _claim(statement: str, ident: int = 1) -> Claim:
    claim = Claim(id=ident, statement=statement)
    return claim


def test_validator_drops_a_sentence_citing_an_unknown_claim():
    claims = {1: _claim("The cell delivers 450 Wh/kg.")}
    body = WikiBody(
        title="t",
        summary=[WikiSentence(text="A completely unrelated statement.", claim_ids=[99])],
    )
    cleaned, dropped = validate_body(body, claims)
    assert cleaned.summary == []
    assert dropped and "99" in dropped[0]["reason"]


def test_validator_drops_a_sentence_with_an_unsupported_number():
    claims = {1: _claim("The cell delivers 450 Wh/kg.")}
    body = WikiBody(
        title="t",
        summary=[WikiSentence(text="The cell delivers 900 Wh/kg.", claim_ids=[1])],
    )
    cleaned, dropped = validate_body(body, claims)
    assert cleaned.summary == []
    assert dropped and "900" in dropped[0]["reason"]


def test_validator_keeps_a_sentence_its_claims_support():
    claims = {1: _claim("The cell delivers 450 Wh/kg.")}
    body = WikiBody(
        title="t",
        summary=[WikiSentence(text="The cell delivers 450 Wh/kg.", claim_ids=[1])],
    )
    cleaned, dropped = validate_body(body, claims)
    assert len(cleaned.summary) == 1
    assert dropped == []


def test_validator_accepts_a_history_sentence_citing_both_values():
    """A sentence naming both the old and the new number is exactly right."""
    claims = {
        1: _claim("Downtown traffic fell by 18%.", 1),
        2: _claim("Revised data shows downtown traffic fell by 11%.", 2),
    }
    body = WikiBody(
        title="t",
        summary=[
            WikiSentence(
                text=(
                    "Downtown traffic fell by 18% after the charge, and the revised data "
                    "shows downtown traffic fell by 11%."
                ),
                claim_ids=[1, 2],
            )
        ],
    )
    cleaned, dropped = validate_body(body, claims)
    assert len(cleaned.summary) == 1, dropped


def test_validator_drops_an_empty_section():
    claims = {1: _claim("A statement.")}
    body = WikiBody(
        title="t",
        summary=[],
        sections=[WikiSection(id="empty", heading="Empty", kind="details", sentences=[])],
    )
    cleaned, _ = validate_body(body, claims)
    assert cleaned.sections == []


def test_paraphrase_gate_rejects_an_off_topic_sentence():
    """A generated sentence must actually be about the claims it cites."""
    claims = {1: _claim("The cell delivers 450 Wh/kg of energy density.")}
    on_topic = WikiBody(title="t", summary=[WikiSentence(text="The cell delivers 450 Wh/kg.", claim_ids=[1])])
    off_topic = WikiBody(
        title="t",
        summary=[WikiSentence(text="Bananas are yellow in the tropics.", claim_ids=[1])],
    )
    assert validate_body(on_topic, claims, allow_paraphrase=True)[1] == []
    assert validate_body(off_topic, claims, allow_paraphrase=True)[1]


def test_dispute_coverage_detection():
    claims = {1: _claim("A", 1), 2: _claim("B", 2), 3: _claim("C", 3)}
    both = WikiBody(
        title="t",
        summary=[WikiSentence(text="A and B disagree.", claim_ids=[1, 2])],
    )
    one_side = WikiBody(title="t", summary=[WikiSentence(text="A.", claim_ids=[1])])
    assert covers_dispute(both, claims, {1: "A", 2: "B"})
    assert not covers_dispute(one_side, claims, {1: "A", 2: "B"})
    assert covers_dispute(one_side, claims, {1: "A"}), "no dispute, nothing to cover"


# --- lint and export -----------------------------------------------------


def test_lint_reports_no_uncited_sentences(wiki):
    session, user_id = wiki
    report = lint_wiki(session, user_id)
    kinds = {item["kind"] for item in report["items"]}
    assert "uncited_sentence" not in kinds, report["items"]
    assert "unknown_claim_reference" not in kinds, report["items"]


def test_lint_closes_findings_that_stop_reproducing(wiki):
    """A finding whose cause is gone is closed, not deleted -- the history survives."""
    session, user_id = wiki
    seeded = LintFinding(user_id=user_id, page_id=None, kind="broken_link", severity="error", detail={})
    session.add(seeded)
    session.commit()

    lint_wiki(session, user_id)
    session.refresh(seeded)
    assert seeded.resolved_at is not None, "a finding that no longer reproduces must be closed"


def test_export_zip_contains_pages_index_and_sources(wiki):
    from app.wiki.export import export_zip

    session, user_id = wiki
    payload = export_zip(session, user_id)
    archive = zipfile.ZipFile(io.BytesIO(payload))
    names = archive.namelist()
    assert "wiki/_index.md" in names
    assert any(n.startswith("wiki/") and n.endswith(".md") for n in names)
    assert any(n.startswith("sources/") for n in names)

    index = archive.read("wiki/_index.md").decode()
    assert "[[hx-2]]" in index
    page_md = archive.read("wiki/hx-2.md").decode()
    assert page_md.startswith("---"), "exported pages carry front matter"
    assert "revision:" in page_md


def test_wiki_is_tenant_scoped(wiki):
    session, user_id = wiki
    from sqlalchemy import delete

    from app.core.security import hash_password
    from app.models.user import User
    from app.wiki.export import export_zip

    other_user = User(
        id=uuid.uuid4(),
        email=f"wiki_other_{uuid.uuid4().hex[:8]}@test.local",
        password_hash=hash_password("test-pass-123"),
    )
    session.add(other_user)
    session.commit()
    try:
        assert _pages(session, other_user.id) == []
        archive = zipfile.ZipFile(io.BytesIO(export_zip(session, other_user.id)))
        assert archive.namelist() == ["wiki/_index.md"]
        index_content = archive.read("wiki/_index.md").decode()
        assert "[[hx-2]]" not in index_content
    finally:
        session.execute(delete(User).where(User.id == other_user.id))
        session.commit()


def test_compile_failure_marks_the_page_not_the_request(wiki, monkeypatch):
    """A page that cannot compile is recorded as failed; the call still returns.

    Runs on the module's own session rather than `fresh_db`: the module fixture
    holds an open transaction, and a second connection inserting into `wiki_pages`
    would block on it until the test timed out.
    """
    from app.wiki import compiler as compiler_module

    session, user_id = wiki
    claim = (
        session.execute(select(Claim).where(Claim.user_id == user_id, Claim.status == "active").limit(1))
        .scalars()
        .first()
    )
    assert claim is not None

    topic = Topic(user_id=user_id, slug="broken-topic", label="broken topic")
    session.add(topic)
    session.flush()
    page = WikiPage(user_id=user_id, topic_id=topic.id, slug="broken-page", title="broken", status="dirty")
    session.add(page)
    session.flush()

    # Give the page real inputs, or `compile_page` returns early on "no claims" and
    # never reaches the composer this test is about.
    monkeypatch.setattr(compiler_module, "page_inputs", lambda *_: [{"id": claim.id, "status": "active"}])

    def boom(*args, **kwargs):
        raise RuntimeError("no route")

    monkeypatch.setattr(compiler_module, "_compose", boom)
    result = compile_page(session, page)

    assert page.status == "failed"
    assert "no route" in page.error
    assert "error" in result


def test_wiki_page_dirty_on_model_change():
    """DOM-14: Changing model name must produce a different input_hash."""
    inputs = [{"id": 1, "claim_hash": "h1", "status": "active"}]
    h1 = input_hash(inputs, prompt_version="v1", model="model-v1", provider="fake")
    h2 = input_hash(inputs, prompt_version="v1", model="model-v2", provider="fake")
    assert h1 != h2, "changing model name must change input hash and make page dirty"


def test_reject_dispute_marks_page_dirty_and_removes_dispute(wiki):
    """DOM-14: Rejecting a dispute changes input_hash and recompilation removes the disputed section."""
    session, user_id = wiki
    from app.models.claim import ClaimRelation

    hx2_page = (
        session.execute(select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.slug == "hx-2"))
        .scalars()
        .first()
    )
    assert hx2_page is not None
    # Recompile to ensure clean state
    compile_page(session, hx2_page, force=True)
    session.commit()

    old_hash = hx2_page.input_hash
    raw_inputs = hx2_page.compiled_input or []
    claims_list = raw_inputs if isinstance(raw_inputs, list) else raw_inputs.get("claims", [])
    claim_ids = {c["id"] for c in claims_list}
    rels = (
        session.execute(
            select(ClaimRelation).where(
                ClaimRelation.user_id == user_id,
                ClaimRelation.relation == "contradicts",
                (ClaimRelation.a_claim_id.in_(claim_ids)) | (ClaimRelation.b_claim_id.in_(claim_ids)),
            )
        )
        .scalars()
        .all()
    )
    assert len(rels) > 0, "must find contradiction relations"

    # Reject the relations
    for rel in rels:
        rel.user_label = "rejected"
    session.flush()

    from app.wiki.compiler import page_inputs

    new_inputs = page_inputs(session, hx2_page)
    new_hash = input_hash(new_inputs, prompt_version="v1", model="heuristic")
    assert new_hash != old_hash, "rejecting dispute must change input_hash"

    # Recompile page
    compile_page(session, hx2_page)
    session.commit()
    section_ids = [s.get("id") for s in hx2_page.body.get("sections", [])]
    assert "disputed" not in section_ids, "disputed section must disappear after dispute is rejected"


def test_demo_page_first_sentence_is_not_superseded(wiki):
    """DOM-15: Summary and overview must only cite active claims, not superseded ones."""
    session, user_id = wiki
    hx2_page = (
        session.execute(select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.slug == "hx-2"))
        .scalars()
        .first()
    )
    assert hx2_page is not None
    compile_page(session, hx2_page, force=True)
    session.commit()

    summary = hx2_page.body.get("summary", [])
    assert summary, "page must have summary"
    first_sentence = summary[0]
    for cid in first_sentence.get("claim_ids", []):
        c = session.get(Claim, cid)
        assert c is not None
        assert c.status == "active", f"first sentence claim {cid} must be active, not {c.status}"


def test_lint_wiki_tenant_isolated(wiki):
    """SEC-14: Lint findings must not contain another tenant's page id."""
    session, user_id = wiki
    from app.core.security import hash_password
    from app.models.user import User

    other_id = uuid.uuid4()
    session.add(
        User(
            id=other_id,
            email=f"lint_iso_{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("pw-123"),
        )
    )
    session.commit()
    try:
        topic_b = Topic(user_id=other_id, slug="topic-b", label="Topic B")
        session.add(topic_b)
        session.flush()
        page_b = WikiPage(
            user_id=other_id, topic_id=topic_b.id, slug="page-b", title="Page B", status="clean"
        )
        session.add(page_b)
        session.flush()

        report_a = lint_wiki(session, user_id)
        report_b = lint_wiki(session, other_id)

        pages_in_a = {item.get("page_id") for item in report_a["items"] if item.get("page_id")}
        pages_in_b = {item.get("page_id") for item in report_b["items"] if item.get("page_id")}

        assert page_b.id not in pages_in_a, "other user's page id must not leak into user A lint"
        user_pages = set(session.execute(select(WikiPage.id).where(WikiPage.user_id == user_id)).scalars())
        assert not (pages_in_b & user_pages), "user A's page id must not leak into other user lint"
    finally:
        session.rollback()
        session.execute(text("DELETE FROM wiki_pages WHERE user_id = :u"), {"u": other_id})
        session.execute(text("DELETE FROM topics WHERE user_id = :u"), {"u": other_id})
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": other_id})
        session.commit()


def test_wiki_footnotes_api_and_marker_alignment(wiki, engine):
    """FE-5: GET /v1/wiki/pages/{slug} returns ordered footnotes matching [^n] markers."""
    import re

    from fastapi.testclient import TestClient

    from app.core.db import get_db
    from app.core.security import create_access_token
    from app.main import create_app

    session, user_id = wiki
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session

    try:
        with TestClient(app) as client:
            client.headers.update({"Authorization": f"Bearer {create_access_token(user_id)}"})
            resp = client.get("/v1/wiki/pages/hx-2")
            assert resp.status_code == 200, resp.text
            data = resp.json()

            footnotes = data.get("footnotes", [])
            assert footnotes, "page must have footnotes"
            body_md = data.get("body_md", "")

            # Extract all [^n] markers in markdown
            markers = [int(m) for m in re.findall(r"\[\^(\d+)\]", body_md)]
            assert markers, "markdown must contain [^n] markers"
            unique_markers = sorted(set(markers))

            # Footnote numbers must match markers exactly
            fn_numbers = [f["n"] for f in footnotes]
            assert fn_numbers == unique_markers, "footnotes numbers must match markdown markers"

            # Check quotes
            for f in footnotes:
                claim = session.get(Claim, f["claim_id"])
                assert claim is not None
                assert f["quote"] == claim.quote
                assert f["char_start"] == claim.char_start
                assert f["char_end"] == claim.char_end
    finally:
        app.dependency_overrides.clear()
