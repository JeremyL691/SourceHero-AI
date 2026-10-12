"""K5.5 incremental wiki patches and K5.6 section diffs between revisions.

The live path is switched on by monkeypatching `LLMClient.mode`; the provider stays the
fake one, so nothing leaves the process. A "claim arrived since the last compile" is
simulated by dropping one id from the page's `compiled_input`.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.llm.providers.fake import get_fake_provider
from app.llm.schemas import WikiBody, WikiPatch, WikiSection, WikiSentence
from app.models.claim import Claim
from app.models.wiki import WikiPage, WikiPageRevision
from app.wiki.compiler import compile_page
from app.wiki.patch import changed_inputs, merge_patch
from app.wiki.topics import plan_topics


def _sec(sid: str, text: str = "x", claim_id: int = 1) -> WikiSection:
    return WikiSection(
        id=sid,
        heading=sid.title(),
        kind="details",
        sentences=[WikiSentence(text=text, claim_ids=[claim_id])],
    )


# --- pure: change detection and merge -----------------------------------


def test_changed_inputs_sees_added_removed_and_changed_claims():
    old = [
        {"id": 1, "status": "active", "relations": []},
        {"id": 2, "status": "active", "relations": [(3, "supports", "x", None, None)]},
        {"id": 3, "status": "active", "relations": []},
    ]
    # Stored inputs come back from JSON, so the tuple above is a list in the database.
    stored = json.loads(json.dumps(old))
    new = [
        {"id": 1, "status": "superseded", "relations": []},
        {"id": 2, "status": "active", "relations": [(3, "supports", "x", None, None)]},
        {"id": 4, "status": "active", "relations": []},
    ]
    change = changed_inputs(stored, new)
    assert change.added_ids == [4]
    assert change.removed_ids == [3]
    assert change.changed_ids == [1]
    assert change.total == 3


def test_unchanged_inputs_are_not_reported_even_when_stored_as_json():
    inputs = [{"id": 1, "relations": [(2, "a", "b", None, None)]}]
    stored = json.loads(json.dumps(inputs))
    assert changed_inputs(stored, inputs).total == 0


def test_merge_keeps_untouched_sections_verbatim_and_applies_the_patch():
    old = WikiBody(
        title="t",
        summary=[WikiSentence(text="s", claim_ids=[1])],
        sections=[_sec("overview"), _sec("details"), _sec("history")],
    )
    patch = WikiPatch(
        updated_sections=[_sec("details", text="new details", claim_id=2)],
        new_sections=[_sec("recent", claim_id=3)],
        removed_section_ids=["history"],
    )
    merged = merge_patch(old, patch)
    ids = [s.id for s in merged.sections]
    assert ids == ["overview", "details", "recent"]
    assert merged.sections[0] == old.sections[0]
    assert merged.sections[1].sentences[0].text == "new details"
    assert merged.summary == old.summary


def test_merge_with_an_empty_patch_is_the_identity():
    old = WikiBody(title="t", sections=[_sec("a"), _sec("b")])
    assert merge_patch(old, WikiPatch()) == old


# --- compile flow (database) ---------------------------------------------


@pytest.fixture
def compiled(db, no_inline_jobs, user_factory):
    """One user's demo corpus, compiled heuristically (no model involved)."""
    from app.demo.seed import drain, seed_demo

    _client, me = user_factory()
    user_id = uuid.UUID(me["id"])
    seed_demo(db, user_id)
    drain(db)
    plan_topics(db, user_id)
    pages = list(
        db.execute(select(WikiPage).where(WikiPage.user_id == user_id).order_by(WikiPage.slug)).scalars()
    )
    for page in pages:
        compile_page(db, page)
    db.flush()
    # The page with the most sections gives the patch tests something to keep and to remove.
    page = max(pages, key=lambda p: len((p.body or {}).get("sections") or []))
    assert len((page.body or {}).get("sections") or []) >= 2, [p.slug for p in pages]
    return db, _client, page


@pytest.fixture
def live(monkeypatch):
    """Make the wiki compiler see a live model; the provider stays the fake one."""
    from app.llm.client import LLMClient

    monkeypatch.setattr(LLMClient, "mode", property(lambda self: "live"))
    provider = get_fake_provider()
    yield provider
    provider.clear_script()


def _forget_one_claim(page: WikiPage) -> int:
    """Pretend the page's first claim arrived after the last compile."""
    inputs = list(page.compiled_input or [])
    dropped_id = inputs[0]["id"]
    page.compiled_input = inputs[1:]
    return dropped_id


@pytest.mark.db
def test_patch_leaves_untouched_sections_byte_identical(compiled, live):
    db, _client, page = compiled
    before = {s["id"]: json.dumps(s, sort_keys=True) for s in page.body["sections"]}
    _forget_one_claim(page)

    result = compile_page(db, page)

    assert result.get("method", "").endswith(":patch"), result
    after = {s["id"]: json.dumps(s, sort_keys=True) for s in page.body["sections"]}
    for sid, text in before.items():
        assert after[sid] == text, f"section {sid!r} was rewritten by a patch"
    assert "recent-updates" in after


@pytest.mark.db
def test_removed_section_disappears_after_a_patch(compiled, live):
    db, _client, page = compiled
    victim = page.body["sections"][-1]["id"]
    live.script("wiki_patch", [json.dumps({"removed_section_ids": [victim]})])
    _forget_one_claim(page)

    result = compile_page(db, page)

    assert result.get("method", "").endswith(":patch"), result
    assert victim not in [s["id"] for s in page.body["sections"]]


@pytest.mark.db
def test_patch_citing_a_missing_claim_falls_back_to_full_compile(compiled, live):
    db, _client, page = compiled
    bogus = {
        "new_sections": [
            {
                "id": "ghost",
                "heading": "Ghost",
                "kind": "details",
                "sentences": [{"text": "Nothing here.", "claim_ids": [987654321]}],
            }
        ]
    }
    live.script("wiki_patch", [json.dumps(bogus)])
    _forget_one_claim(page)

    result = compile_page(db, page)

    assert result.get("method", "").startswith("llm:")
    assert not result["method"].endswith(":patch"), result
    assert "ghost" not in [s["id"] for s in page.body["sections"]]


@pytest.mark.db
def test_unchanged_body_leaves_the_revision_unchanged(compiled):
    db, _client, page = compiled
    revision = page.revision
    rows = db.execute(select(WikiPageRevision).where(WikiPageRevision.page_id == page.id)).scalars().all()
    count = len(rows)

    compile_page(db, page, force=True)

    assert page.revision == revision
    assert (
        len(db.execute(select(WikiPageRevision).where(WikiPageRevision.page_id == page.id)).scalars().all())
        == count
    )


@pytest.mark.db
def test_patch_records_a_section_level_change_summary(compiled, live):
    db, _client, page = compiled
    _forget_one_claim(page)

    compile_page(db, page)

    latest = (
        db.execute(
            select(WikiPageRevision)
            .where(WikiPageRevision.page_id == page.id)
            .order_by(WikiPageRevision.revision.desc())
        )
        .scalars()
        .first()
    )
    assert latest.revision == page.revision
    assert latest.change_summary == "0 updated, 1 added, 0 removed sections"


@pytest.mark.db
def test_forced_compile_never_patches(compiled, live, monkeypatch):
    """POST /compile (force) must run the full compile even when a patch would do."""
    db, client, page = compiled
    scripted = json.dumps({"removed_section_ids": [page.body["sections"][-1]["id"]]})
    live.script("wiki_patch", [scripted])
    _forget_one_claim(page)
    db.flush()

    resp = client.post(f"/v1/wiki/pages/{page.slug}/compile")

    assert resp.status_code == 200, resp.text
    db.refresh(page)
    assert not page.compile_method.endswith(":patch"), page.compile_method
    assert live.scripted("wiki_patch") == scripted, "the forced compile must not consume a patch"


# --- K5.6: section diff between revisions --------------------------------


@pytest.mark.db
def test_revision_diff_lists_added_removed_and_changed_sections(compiled, live):
    db, client, page = compiled
    old_sections = page.body["sections"]
    assert len(old_sections) >= 2
    first, last = old_sections[0], old_sections[-1]
    claim_id = page.compiled_input[0]["id"]
    statement = db.get(Claim, claim_id).statement

    # Revision N: the heuristic page. Revision N+1: a patch that renames the first
    # section, drops the last one and adds a fresh one.
    patch = {
        "updated_sections": [{**first, "heading": "Renamed"}],
        "new_sections": [
            {
                "id": "fresh",
                "heading": "Fresh",
                "kind": "details",
                "sentences": [{"text": statement, "claim_ids": [claim_id]}],
            }
        ],
        "removed_section_ids": [last["id"]],
    }
    live.script("wiki_patch", [json.dumps(patch)])
    from_revision = page.revision
    _forget_one_claim(page)
    result = compile_page(db, page)
    assert result.get("method", "").endswith(":patch"), result
    assert page.revision == from_revision + 1

    resp = client.get(f"/v1/wiki/pages/{page.slug}/revisions/{from_revision}/diff")

    assert resp.status_code == 200, resp.text
    sections = resp.json()["sections"]
    assert sections["added"] == ["fresh"]
    assert sections["removed"] == [last["id"]]
    assert sections["changed"] == [first["id"]]
