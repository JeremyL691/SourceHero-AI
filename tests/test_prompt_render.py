"""H4.3 / TEST-6 / LLM-1: Prompt rendering smoke test suite.

Every prompt in `app/llm/prompts/*.md` is rendered using its real caller context builder
(pure functions, no hand-crafted dicts), asserting that template variables and caller
parameters never drift out of sync under Jinja2 StrictUndefined.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from jinja2 import UndefinedError

from app.claims.extract import build_claim_extract_context
from app.claims.relate import build_relation_judge_context
from app.llm.prompts import PROMPT_DIR, render
from app.temporal.belief_diff import build_diff_digest_context
from app.verify.answer import build_answer_context
from app.verify.evidence import EvidenceSpan
from app.verify.verifier import SentenceVerdict, build_verify_context
from app.wiki.compiler import build_wiki_compile_context, build_wiki_patch_context
from app.wiki.topics import build_topic_label_context


def test_render_answer():
    hits = [
        SimpleNamespace(
            title="Battery Breakthrough",
            source_name="Energy Daily",
            published_at=datetime(2026, 5, 1, tzinfo=UTC),
            text="Silicon anode density reached 450 Wh/kg in testing.",
        )
    ]
    disputes = [{"label": "density conflict", "a": "450 Wh/kg", "b": "380 Wh/kg"}]
    ctx = build_answer_context("What is the energy density?", hits, disputes)
    messages, version = render("answer", **ctx)
    assert len(messages) >= 2
    assert "What is the energy density?" in messages[-1].content
    assert '<source id="1">' in messages[-1].content
    assert "450 Wh/kg" in messages[-1].content
    assert "Known disagreements among these sources:" in messages[-1].content


def test_render_claim_extract():
    ctx = build_claim_extract_context(
        title="Lab Report",
        passage="The battery achieved 450 Wh/kg on 2026-05-01.",
        published_at="2026-05-01",
    )
    messages, version = render("claim_extract", **ctx)
    assert len(messages) >= 2
    assert "Lab Report" in messages[-1].content
    assert '<source id="Lab Report">' in messages[-1].content
    assert "450 Wh/kg" in messages[-1].content


def test_render_diff_digest():
    payload = {
        "updated": [{"claim_id": 1, "statement": "Target raised to 500 Wh/kg"}],
        "conflicts": [],
        "stale_notes": [],
        "new": [{"claim_id": 2, "statement": "New lab opened"}],
    }
    start = datetime(2026, 5, 1, tzinfo=UTC)
    end = datetime(2026, 6, 1, tzinfo=UTC)
    ctx = build_diff_digest_context(payload, start=start, end=end)
    messages, version = render("diff_digest", **ctx)
    assert len(messages) >= 2
    assert "Period: 2026-05-01 – 2026-06-01" in messages[-1].content
    assert '<source id="diff_payload">' in messages[-1].content


def test_render_relation_judge():
    pairs = [
        {
            "index": 1,
            "a": {
                "source": "Doc A",
                "date": "2026-05-01",
                "statement": "Energy density is 450 Wh/kg.",
                "quote": "energy density is 450 Wh/kg",
            },
            "b": {
                "source": "Doc B",
                "date": "2026-06-01",
                "statement": "Energy density is 380 Wh/kg.",
                "quote": "energy density is 380 Wh/kg",
            },
        }
    ]
    ctx = build_relation_judge_context(pairs)
    messages, version = render("relation_judge", **ctx)
    assert len(messages) >= 2
    assert "Pair 1:" in messages[-1].content
    assert '<source id="pair-1">' in messages[-1].content
    assert "450 Wh/kg" in messages[-1].content


def test_render_topic_label():
    claims = [
        "Silicon battery achieves 450 Wh/kg density.",
        "HX-2 battery starts pilot production.",
    ]
    ctx = build_topic_label_context(claims)
    messages, version = render("topic_label", **ctx)
    assert len(messages) >= 2
    assert '<source id="claims">' in messages[-1].content
    assert "HX-2" in messages[-1].content


def test_render_verify():
    import uuid

    span = EvidenceSpan(
        chunk_id=1,
        document_id=uuid.uuid4(),
        version_id=10,
        source_name="Lab Report",
        published_at=datetime(2026, 5, 1, tzinfo=UTC),
        char_start=0,
        char_end=50,
        text="The battery achieved 450 Wh/kg in testing.",
    )
    verdict = SentenceVerdict(
        index=0,
        text="The battery density is 450 Wh/kg.",
        char_start=0,
        char_end=34,
        verdict="supported",
        supporting=[span],
        contradicting=[],
        all_evidence=[span],
    )
    ctx = build_verify_context([verdict])
    messages, version = render("verify", **ctx)
    assert len(messages) >= 2
    assert "Sentence 1: The battery density is 450 Wh/kg." in messages[-1].content
    assert '<source id="1">(1) Lab Report' in messages[-1].content


def test_render_wiki_compile():
    claims_payload = [
        {
            "id": 1,
            "source": "Lab Report",
            "date": "2026-05-01",
            "status": "active",
            "camp": "A",
            "statement": "Battery achieved 450 Wh/kg.",
        }
    ]
    ctx = build_wiki_compile_context(
        topic="Solid State Battery",
        claims=claims_payload,
        related_slugs=["hx-2"],
        language="English",
    )
    messages, version = render("wiki_compile", **ctx)
    assert len(messages) >= 2
    assert "Topic: Solid State Battery" in messages[-1].content
    assert '<source id="1">[1] (Lab Report, 2026-05-01, camp A)' in messages[-1].content
    assert 'Topics you may list in "see_also": hx-2' in messages[-1].content


def test_render_wiki_patch():
    claims = [
        {
            "id": 1,
            "source": "Lab Report",
            "date": "2026-05-01",
            "status": "active",
            "statement": "Battery achieved 450 Wh/kg.",
        }
    ]
    ctx = build_wiki_patch_context(
        topic="Solid State Battery",
        page_json='{"title": "Solid State Battery"}',
        added=claims,
        removed_ids=[2],
        changed=[],
        claims=claims,
    )
    messages, version = render("wiki_patch", **ctx)
    assert len(messages) >= 2
    assert "Topic: Solid State Battery" in messages[-1].content
    assert '<source id="current_page">' in messages[-1].content
    assert "Removed claim ids: 2" in messages[-1].content


def test_all_prompts_covered():
    """Every prompt file in app/llm/prompts/*.md must have an explicit test."""
    prompt_files = {p.stem for p in PROMPT_DIR.glob("*.md")}
    expected = {
        "answer",
        "claim_extract",
        "diff_digest",
        "relation_judge",
        "topic_label",
        "verify",
        "wiki_compile",
        "wiki_patch",
    }
    assert prompt_files == expected


def test_verify_fails_with_old_mismatched_variables():
    """TEST-6 / LLM-1 regression guard: calling verify with unnested evidence raises UndefinedError."""
    with pytest.raises(UndefinedError):
        # The pre-H3.8 bug passed evidence directly without items[i].evidence nesting
        render("verify", evidence=[{"id": 1, "text": "evidence"}])
